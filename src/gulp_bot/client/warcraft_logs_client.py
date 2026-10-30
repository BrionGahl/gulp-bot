"""Client for the Warcraft Logs v2 API.

The API is public-data only: it authenticates with a guild's own client credentials (no user
authorization), so it can read public and unlisted reports but not private ones. See
<https://www.warcraftlogs.com/api/docs>.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from ..core.http_client import HttpClient, RequestFailed, RequestTimeout, Response, error_body, status_text
from ..exception.warcraft_logs_exception import WarcraftLogsError
from ..mapper.warcraft_logs_response_mapper import (
    actors_from_data,
    data_from_response,
    deaths_from_data,
    player_entries_from_table,
    report_from_json,
    report_object,
    report_summaries_from_data,
)
from ..schema.guild_settings_schema import WclCredentials
from ..schema.wcl_report_schema import WIPE_CUTOFF, Actor, DeathEntry, PlayerEntry, Report, ReportSummary

TOKEN_URL = "https://www.warcraftlogs.com/oauth/token"
API_URL = "https://www.warcraftlogs.com/api/v2/client"

# Difficulty ID of Mythic raids in the Warcraft Logs API.
MYTHIC_DIFFICULTY = 5

# The most fights asked about in one query. An uncut deaths table came back with exactly 200 entries,
# which looks like a cap; with the cutoff a fight yields about two entries, so this stays well under it.
FIGHTS_PER_QUERY = 50

# The most enemies asked about in one damage done query, a table each. A tracked enemy with many adds of the
# same name has an actor each, and a query with too many tables costs more of the API's hourly points.
TARGETS_PER_QUERY = 25

# How many of the guild's newest reports are searched for one from the registered uploader.
REPORTS_TO_SEARCH = 25

# How long a request to Warcraft Logs, token or query, may take before the bot gives up, in seconds.
# Without a limit a hung connection would leave the command waiting until Discord's 15 minutes ran out.
REQUEST_TIMEOUT = 30.0

# A token is replaced this many seconds before it expires, so one is never sent just as it lapses.
TOKEN_REFRESH_MARGIN = 60.0

TIMEOUT_MESSAGE = "Warcraft Logs took too long to answer. Try again in a moment."

# The queries take their values as GraphQL variables, never spliced into the text, so a report code typed
# into a command can't change what is asked. The one thing built into a query is the list of ability IDs,
# which are numbers.
REPORT_QUERY = (
    "query($code: String!) { reportData { report(code: $code) { "
    "code title startTime owner { id } "
    f"fights(difficulty: {MYTHIC_DIFFICULTY}, killType: Encounters) {{ id name kill inProgress startTime endTime }} }} }} }}"
)

GUILD_REPORTS_QUERY = "query($guildID: Int, $limit: Int) { reportData { reports(guildID: $guildID, limit: $limit) { data { code startTime owner { id } } } } }"

DEATHS_QUERY = (
    "query($code: String!, $fightIDs: [Int]) { reportData { report(code: $code) { "
    f"table(dataType: Deaths, fightIDs: $fightIDs, wipeCutoff: {WIPE_CUTOFF}) }} }} }}"
)

# Every death in some pulls, with no cutoff: the replay needs to see the wipe itself.
ALL_DEATHS_QUERY = "query($code: String!, $fightIDs: [Int]) { reportData { report(code: $code) { table(dataType: Deaths, fightIDs: $fightIDs) } } }"

# The enemies of a report: their IDs in it, which the damage done tables filter by, and their names.
ACTORS_QUERY = 'query($code: String!) { reportData { report(code: $code) { masterData { actors(type: "NPC") { id name subType } } } } }'

# The damage done to everything in one pull between two moments, given in milliseconds since the start of
# the report: every target together.
ALL_DAMAGE_DONE_QUERY = (
    "query($code: String!, $fightIDs: [Int], $startTime: Float, $endTime: Float) { reportData { report(code: $code) { "
    "table(dataType: DamageDone, fightIDs: $fightIDs, startTime: $startTime, endTime: $endTime) } } }"
)


def table_alias(index: int) -> str:
    """The alias of the table for the ability at `index` in an abilities query."""
    return f"a{index}"


def ability_tables_query(data_type: str, ability_ids: Sequence[int], cut_off: bool) -> str:
    """One table of `data_type` per ability, each filtered to that ability and so listing the players who
    have it (took the damage, or cast it). `cut_off` asks for the cutoff to be applied. (Not `viewBy:
    Ability`, whose per-ability player lists hold only the top five.)"""
    cut = f", wipeCutoff: {WIPE_CUTOFF}" if cut_off else ""
    tables = "".join(
        f" {table_alias(index)}: table(dataType: {data_type}, fightIDs: $fightIDs, abilityID: {int(ability_id)}{cut})"
        for index, ability_id in enumerate(ability_ids)
    )
    return f"query($code: String!, $fightIDs: [Int]) {{ reportData {{ report(code: $code) {{{tables} }} }} }}"


def target_alias(index: int) -> str:
    """The alias of the table for the target at `index` in a damage done query."""
    return f"t{index}"


def damage_done_tables_query(target_ids: Sequence[int]) -> str:
    """One damage done table per target (an enemy's ID in the report), each listing the players who damaged
    it in one pull between two moments, given in milliseconds since the start of the report. No cutoff:
    the moment is chosen by whoever asks."""
    tables = "".join(
        f" {target_alias(index)}: table(dataType: DamageDone, fightIDs: $fightIDs, startTime: $startTime, endTime: $endTime, targetID: {int(target_id)})"
        for index, target_id in enumerate(target_ids)
    )
    return f"query($code: String!, $fightIDs: [Int], $startTime: Float, $endTime: Float) {{ reportData {{ report(code: $code) {{{tables} }} }} }}"


def _chunks(items: list[int], size: int) -> Iterable[list[int]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


@dataclass
class _AccessToken:
    value: str
    expires_at: float


class WarcraftLogsClient:
    """The Warcraft Logs v2 API, with one guild's client credentials."""

    def __init__(self, http: HttpClient, client_id: str, client_secret: str):
        self._http = http
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: _AccessToken | None = None
        self._token_lock = asyncio.Lock()
        # Attributes rather than constants so a test can point the client at a server of its own.
        self.token_url = TOKEN_URL
        self.api_url = API_URL
        self.timeout = REQUEST_TIMEOUT

    def __repr__(self) -> str:
        # So that the client secret can never end up in a log line.
        return "WarcraftLogsClient(...)"

    async def fetch_report(self, code: str) -> Report:
        """A report with its Mythic boss fights."""
        data = await self.query(REPORT_QUERY, {"code": code})
        return report_from_json(report_object(data))

    async def fetch_guild_reports(self, guild_id: int) -> list[ReportSummary]:
        """The guild's newest reports, from any uploader."""
        return report_summaries_from_data(await self.query(GUILD_REPORTS_QUERY, {"guildID": guild_id, "limit": REPORTS_TO_SEARCH}))

    async def fetch_counted_deaths(self, code: str, fight_ids: list[int]) -> list[DeathEntry]:
        """Every counted death in `fight_ids`: the first `WIPE_CUTOFF` of each pull."""
        deaths: list[DeathEntry] = []
        for chunk in _chunks(fight_ids, FIGHTS_PER_QUERY):
            deaths.extend(deaths_from_data(await self.query(DEATHS_QUERY, {"code": code, "fightIDs": chunk})))
        return deaths

    async def fetch_all_deaths(self, code: str, fight_id: int) -> list[DeathEntry]:
        """Every death in one pull, with no cutoff."""
        return deaths_from_data(await self.query(ALL_DEATHS_QUERY, {"code": code, "fightIDs": [fight_id]}))

    async def fetch_ability_tables(self, code: str, fight_ids: list[int], data_type: str, ability_ids: list[int], cut_off: bool) -> list[list[PlayerEntry]]:
        """For each of `ability_ids`, in order, the player entries of the `data_type` table (damage taken, or
        casts) of each chunk of `fight_ids`, one after the other: pulls beyond `FIGHTS_PER_QUERY` take
        further requests, and a player appears once per chunk. One request covers all the abilities (a
        table each, under an alias)."""
        if not ability_ids:
            return []
        query = ability_tables_query(data_type, ability_ids, cut_off)
        tables: list[list[PlayerEntry]] = [[] for _ in ability_ids]
        for chunk in _chunks(fight_ids, FIGHTS_PER_QUERY):
            report = report_object(await self.query(query, {"code": code, "fightIDs": chunk}))
            for index, entries in enumerate(tables):
                table = report.get(table_alias(index))
                if table is not None:
                    entries.extend(player_entries_from_table(table))
        return tables

    async def fetch_enemies(self, code: str) -> list[Actor]:
        """The enemies (NPCs) of a report."""
        return actors_from_data(await self.query(ACTORS_QUERY, {"code": code}))

    async def fetch_damage_done_tables(self, code: str, fight_id: int, start_ms: int, end_ms: int, target_ids: list[int]) -> dict[int, list[PlayerEntry]]:
        """For each of `target_ids`, the players who damaged it in pull `fight_id` between `start_ms` and
        `end_ms` (milliseconds since the start of the report), and how much. Targets beyond
        `TARGETS_PER_QUERY` take further requests."""
        tables: dict[int, list[PlayerEntry]] = {}
        variables = {"code": code, "fightIDs": [fight_id], "startTime": float(start_ms), "endTime": float(end_ms)}
        for chunk in _chunks(target_ids, TARGETS_PER_QUERY):
            report = report_object(await self.query(damage_done_tables_query(chunk), variables))
            for index, target_id in enumerate(chunk):
                table = report.get(target_alias(index))
                tables[target_id] = player_entries_from_table(table) if table is not None else []
        return tables

    async def fetch_all_damage_done(self, code: str, fight_id: int, start_ms: int, end_ms: int) -> list[PlayerEntry]:
        """The players' damage to every target together in pull `fight_id` between `start_ms` and `end_ms`
        (milliseconds since the start of the report)."""
        variables = {"code": code, "fightIDs": [fight_id], "startTime": float(start_ms), "endTime": float(end_ms)}
        table = report_object(await self.query(ALL_DAMAGE_DONE_QUERY, variables)).get("table")
        return player_entries_from_table(table) if table is not None else []

    async def query(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        """Sends one GraphQL query and returns its data, or raises with the API's own error messages."""
        # Twice: a token the API turns down is dropped and a fresh one is tried once.
        for attempt in range(2):
            token = await self._access_token()
            response = await self._post(self.api_url, headers={"Authorization": f"Bearer {token}"}, json={"query": query, "variables": variables})

            if response.status == 401 and attempt == 0:
                self._token = None
                continue
            if not response.ok:
                raise WarcraftLogsError(f"Warcraft Logs API returned {status_text(response.status)}: {error_body(response.body)}")
            try:
                body = json.loads(response.body)
            except ValueError as e:
                raise WarcraftLogsError(f"error decoding response body: {e}") from None
            return data_from_response(body)
        raise WarcraftLogsError("Warcraft Logs turned down a freshly issued access token.")

    async def _post(self, url: str, **kwargs: Any) -> Response:
        """A POST to the API, with its timeout. A request that times out or can't be sent raises."""
        try:
            return await self._http.request("POST", url, timeout=self.timeout, **kwargs)
        except RequestTimeout:
            raise WarcraftLogsError(TIMEOUT_MESSAGE) from None
        except RequestFailed as e:
            raise WarcraftLogsError(str(e)) from None

    async def _access_token(self) -> str:
        """A valid access token, fetched with the client credentials flow when there is none yet or the one
        held is about to expire."""
        async with self._token_lock:
            if self._token is not None and self._token.expires_at > time.monotonic() + TOKEN_REFRESH_MARGIN:
                return self._token.value

            response = await self._post(self.token_url, form={"grant_type": "client_credentials"}, basic_auth=(self._client_id, self._client_secret))
            if not response.ok:
                raise WarcraftLogsError(f"Warcraft Logs refused the client credentials ({status_text(response.status)}): {error_body(response.body)}")

            try:
                issued = json.loads(response.body)
                value, expires_in = str(issued["access_token"]), float(issued["expires_in"])
            except (ValueError, KeyError, TypeError) as e:
                raise WarcraftLogsError(f"error decoding response body: {e}") from None
            self._token = _AccessToken(value, time.monotonic() + expires_in)
            return value


class WarcraftLogsClientPool:
    """Each guild's Warcraft Logs client, built from the credentials the guild registered (`/settings wcl
    client`) the first time it is needed and kept, so that its access token is reused from one command to
    the next."""

    def __init__(self, http: HttpClient):
        self._http = http
        self._clients: dict[int, tuple[WclCredentials, WarcraftLogsClient]] = {}

    def client_for(self, guild_id: int, credentials: WclCredentials) -> WarcraftLogsClient:
        """The guild's client for `credentials`. A client built from credentials the guild has since
        replaced is dropped, along with its token."""
        held = self._clients.get(guild_id)
        if held is not None and held[0] == credentials:
            return held[1]
        client = WarcraftLogsClient(self._http, credentials.client_id, credentials.client_secret)
        self._clients[guild_id] = (credentials, client)
        return client
