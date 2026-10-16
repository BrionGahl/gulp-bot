"""Client for the Warcraft Logs v2 API, and the overviews built from it.

The API is public-data only: it authenticates with a guild's own client credentials (no user
authorization), so it can read public and unlisted reports but not private ones. See
<https://www.warcraftlogs.com/api/docs>.

The embeds are built as plain data (Discord's JSON shape), so that they can be tested without Discord;
`helper.embed_from_dict` turns one into what discord.py sends.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .ai.text import ascii_lower
from .guild_settings import GuildSettings, WclCredentials, WclSource
from .wcl_consumables import ALL_KINDS, Kind, Registered
from .wcl_mechanics import TrackedAbility
from .web import HttpClient, RequestFailed, RequestTimeout, status_text

log = logging.getLogger(__name__)

TOKEN_URL = "https://www.warcraftlogs.com/oauth/token"
API_URL = "https://www.warcraftlogs.com/api/v2/client"

# Difficulty ID of Mythic raids in the Warcraft Logs API.
MYTHIC_DIFFICULTY = 5

# Only this many deaths in a pull are counted. Every death after the second is the raid wiping rather
# than someone's mistake, so the API is asked to ignore everything after it (`wipeCutoff`). Damage
# taken is cut off the same way: nothing after the last counted death.
WIPE_CUTOFF = 2

# The most fights asked about in one query. An uncut deaths table came back with exactly 200 entries,
# which looks like a cap; with the cutoff a fight yields about two entries, so this stays well under it.
FIGHTS_PER_QUERY = 50

# How many of the guild's newest reports are searched for one from the registered uploader.
REPORTS_TO_SEARCH = 25

# How long a request to Warcraft Logs, token or query, may take before the bot gives up, in seconds.
# Without a limit a hung connection would leave the command waiting until Discord's 15 minutes ran out.
REQUEST_TIMEOUT = 30.0

# A token is replaced this many seconds before it expires, so one is never sent just as it lapses.
TOKEN_REFRESH_MARGIN = 60.0

# The purple of the bot's embeds.
EMBED_COLOUR = 0xAF69EE

# Discord's limits on an embed's title, description and one field's value.
MAX_TITLE_CHARS = 256
MAX_DESCRIPTION_CHARS = 4096
MAX_FIELD_CHARS = 1024
# Discord's limit on all the text in one embed (title, description, field names and values, footer).
MAX_EMBED_CHARS = 6000
# Room kept free in the overview for the footer note that says abilities were left out.
FOOTER_NOTE_RESERVE = 100
# The longest an ability name is shown as, in a title or a field name (Discord allows 256).
MAX_ABILITY_NAME_CHARS = 100

# A wipe is taken to start when this many players die within `WIPE_WINDOW_MS` of each other.
WIPE_DEATHS = 3
WIPE_WINDOW_MS = 2_000.0
# The replay link starts this long before that moment, to show what led up to it.
REPLAY_LEAD_IN_MS = 5_000.0

# How many players each ranking shows.
MOST_DEATHS_SHOWN = 10
FIRST_DEATHS_SHOWN = 5
DAMAGE_SHOWN_IN_OVERVIEW = 5
DAMAGE_SHOWN = 15
CONSUMABLE_PLAYERS_SHOWN = 10

TIMEOUT_MESSAGE = "Warcraft Logs took too long to answer. Try again in a moment."
NOT_PUBLIC = "That report doesn't exist, or isn't public."


class WclError(Exception):
    """A request to Warcraft Logs that got no usable answer. The message is shown to the officer."""


def _blen(text: str) -> int:
    """The length of `text` in UTF-8 bytes: the measure the Rust bot used for its size budgets, which is
    never less than the characters Discord counts."""
    return len(text.encode("utf-8"))


class WarcraftLogsClients:
    """Each guild's Warcraft Logs client, built from the credentials the guild registered (`/settings wcl
    client`) the first time it is needed and kept, so that its access token is reused from one command
    to the next."""

    def __init__(self, http: HttpClient):
        self._http = http
        self._clients: dict[int, tuple[WclCredentials, WarcraftLogs]] = {}

    def for_guild(self, guild_id: int, credentials: WclCredentials) -> WarcraftLogs:
        """The guild's client for `credentials`. A client built from credentials the guild has since
        replaced is dropped, along with its token."""
        held = self._clients.get(guild_id)
        if held is not None and held[0] == credentials:
            return held[1]
        client = WarcraftLogs(self._http, credentials.client_id, credentials.client_secret)
        self._clients[guild_id] = (credentials, client)
        return client


@dataclass
class _AccessToken:
    value: str
    expires_at: float


class WarcraftLogs:
    """Client for the Warcraft Logs v2 API with one guild's client credentials."""

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
        return "WarcraftLogs(...)"

    async def latest_report_code(self, source: WclSource) -> str | None:
        """The newest report in the guild's recent uploads that came from the registered uploader, as its
        report code, or `None` if there isn't one among the guild's newest reports."""
        newest = newest_by_uploader(await self._guild_reports(source), source.uploader_id)
        return None if newest is None else newest.code

    async def latest_report_code_since(self, source: WclSource, earliest_start: datetime) -> str | None:
        """`latest_report_code`, but only if that report was started at or after `earliest_start`. For
        telling one raid night's report from the night before's."""
        newest = newest_by_uploader(await self._guild_reports(source), source.uploader_id)
        return newest.code if newest is not None and newest.started_at_or_after(earliest_start) else None

    async def _guild_reports(self, source: WclSource) -> list[ReportSummary]:
        """The guild's newest reports, from any uploader."""
        data = await self._query(GUILD_REPORTS_QUERY, {"guildID": source.guild_id, "limit": REPORTS_TO_SEARCH})
        try:
            return [ReportSummary.from_json(entry) for entry in data["reportData"]["reports"]["data"]]
        except (KeyError, TypeError) as e:
            raise WclError(f"Warcraft Logs sent something unexpected: {e}") from None

    async def overview(self, code: str, tracked: Sequence[TrackedAbility], ignored: Sequence[str]) -> Overview:
        """Everything the overview shows for one report: its Mythic boss pulls, the deaths in them with
        the cutoff applied, and the damage taken from each of the `tracked` abilities.

        Players named in `ignored` are left out of all of it: they are in no ranking, and what they took
        doesn't count towards any total. (The cutoff is the API's, so an ignored player's death among
        the first two in a pull still uses up one of them.)"""
        report = await self._report(code)
        fight_ids = [fight.id for fight in report.fights if fight.is_finished]
        ignored_names = ignored_set(ignored)

        deaths = [death for death in await self._deaths(code, fight_ids) if not is_ignored(ignored_names, death.name)]
        damage = await self._damage_taken(code, fight_ids, tracked, ignored_names)

        log.info("Fetched Warcraft Logs report %s: %d Mythic pulls, %d counted deaths, %d tracked abilities", code, len(fight_ids), len(deaths), len(tracked))
        return Overview.build(report, deaths, damage, len(ignored))

    async def damage_report(self, code: str, boss: str | None, ability: TrackedAbility, ignored: Sequence[str]) -> DamageReport:
        """Who took damage from one ability, over the pulls of the boss whose name contains `boss`, or of
        every Mythic boss when there is none. Players named in `ignored` are left out, as in `overview`."""
        report = await self._report(code)
        fights = select_fights(report.fights, boss)
        damage = await self._damage_taken(code, [fight.id for fight in fights], [ability], ignored_set(ignored))
        if not damage:
            raise WclError("Warcraft Logs sent no damage data.")

        log.info("Fetched damage taken from %s in Warcraft Logs report %s: %d pulls", ability.name, code, len(fights))
        return DamageReport.build(report, fights, damage[-1], boss, len(ignored))

    async def consumables(self, code: str, boss: str | None, registered: Sequence[Registered], ignored: Sequence[str]) -> ConsumablesReport:
        """How many healthstones and health potions were used, over the pulls of the boss whose name
        contains `boss` or of every Mythic boss when there is none. Every use counts, not only those
        before the cutoff: this is about how much the raid consumed. `registered` says which abilities
        are which kind. Players named in `ignored` are left out, as in `overview`."""
        report = await self._report(code)
        fights = select_fights(report.fights, boss)
        ability_ids = [entry.ability_id for entry in registered]
        totals = await self._ability_totals(code, [fight.id for fight in fights], "Casts", ability_ids, False, ignored_set(ignored))

        log.info("Fetched consumables in Warcraft Logs report %s: %d pulls, %d registered abilities", code, len(fights), len(registered))
        return ConsumablesReport.build(report, fights, boss, registered, totals, len(ignored))

    async def last_wipe(self, code: str) -> LastWipe:
        """The last Mythic boss pull of the report that was a wipe, and where in it to start a replay:
        `REPLAY_LEAD_IN_MS` before the first moment `WIPE_DEATHS` players died within `WIPE_WINDOW_MS`.
        Pulls still being uploaded are skipped."""
        report = await self._report(code)
        finished = [fight for fight in report.fights if fight.is_finished]
        fight = next((fight for fight in reversed(finished) if fight.kill is False), None)
        if fight is None:
            raise WclError("This report has no Mythic boss wipes.")
        pull = sum(1 for other in finished if other.name == fight.name and other.id <= fight.id)

        data = await self._query(ALL_DEATHS_QUERY, {"code": code, "fightIDs": [fight.id]})
        deaths = _deaths_from(data)
        times = [death.timestamp for death in deaths if death.fight == fight.id]

        log.info("Fetched the last wipe of Warcraft Logs report %s: fight %d, %d deaths", code, fight.id, len(times))
        return LastWipe.build(code, fight, pull, times)

    async def _report(self, code: str) -> Report:
        """A report with its Mythic boss fights."""
        data = await self._query(REPORT_QUERY, {"code": code})
        report = (data.get("reportData") or {}).get("report")
        if report is None:
            raise WclError(NOT_PUBLIC)
        return Report.from_json(report)

    async def _deaths(self, code: str, fight_ids: list[int]) -> list[DeathEntry]:
        """Every counted death in `fight_ids`: the first `WIPE_CUTOFF` of each pull."""
        deaths: list[DeathEntry] = []
        for chunk in _chunks(fight_ids, FIGHTS_PER_QUERY):
            deaths.extend(_deaths_from(await self._query(DEATHS_QUERY, {"code": code, "fightIDs": chunk})))
        return deaths

    async def _damage_taken(self, code: str, fight_ids: list[int], abilities: Sequence[TrackedAbility], ignored: set[str]) -> list[AbilityDamage]:
        """The damage each player took from each of `abilities` in `fight_ids`, up to the cutoff in every
        pull, in the order of `abilities`."""
        totals = await self._ability_totals(code, fight_ids, "DamageTaken", [ability.ability_id for ability in abilities], True, ignored)
        return [AbilityDamage.build(ability, players) for ability, players in zip(abilities, totals, strict=False)]

    async def _ability_totals(
        self, code: str, fight_ids: list[int], data_type: str, ability_ids: list[int], cut_off: bool, ignored: set[str]
    ) -> list[dict[str, tuple[str, int]]]:
        """For each of `ability_ids`, in order, what each player has in the `data_type` table (damage
        taken, or casts) over `fight_ids`: `cut_off` limits it to the first deaths in each pull, as for
        damage taken. One request covers all the abilities (a table each, under an alias); pulls beyond
        `FIGHTS_PER_QUERY` take further requests, whose totals are added."""
        if not ability_ids:
            return []

        query = ability_tables_query(data_type, ability_ids, cut_off)
        totals: list[dict[str, tuple[str, int]]] = [{} for _ in ability_ids]
        for chunk in _chunks(fight_ids, FIGHTS_PER_QUERY):
            data = await self._query(query, {"code": code, "fightIDs": chunk})
            tables = (data.get("reportData") or {}).get("report")
            if tables is None:
                raise WclError(NOT_PUBLIC)
            for index, players in enumerate(totals):
                table = tables.get(alias(index))
                if table is not None:
                    add_totals(players, _player_entries(table), ignored)
        return totals

    async def _query(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        """Sends one GraphQL query and returns its data, or raises with the API's own error messages."""
        # Twice: a token the API turns down is dropped and a fresh one is tried once.
        for attempt in range(2):
            token = await self._access_token()
            try:
                response = await self._http.request(
                    "POST",
                    self.api_url,
                    timeout=self.timeout,
                    headers={"Authorization": f"Bearer {token}"},
                    json={"query": query, "variables": variables},
                )
            except RequestTimeout:
                raise WclError(TIMEOUT_MESSAGE) from None
            except RequestFailed as e:
                raise WclError(str(e)) from None

            if response.status == 401 and attempt == 0:
                self._token = None
                continue
            if not response.ok:
                raise WclError(f"Warcraft Logs API returned {status_text(response.status)}: {response.body}")
            try:
                body = json.loads(response.body)
            except ValueError as e:
                raise WclError(f"error decoding response body: {e}") from None
            return parse_response(body)
        raise WclError("Warcraft Logs turned down a freshly issued access token.")

    async def _access_token(self) -> str:
        """A valid access token, fetched with the client credentials flow when there is none yet or the
        one held is about to expire."""
        async with self._token_lock:
            if self._token is not None and self._token.expires_at > time.monotonic() + TOKEN_REFRESH_MARGIN:
                return self._token.value

            try:
                response = await self._http.request(
                    "POST",
                    self.token_url,
                    timeout=self.timeout,
                    form={"grant_type": "client_credentials"},
                    basic_auth=(self._client_id, self._client_secret),
                )
            except RequestTimeout:
                raise WclError(TIMEOUT_MESSAGE) from None
            except RequestFailed as e:
                raise WclError(str(e)) from None
            if not response.ok:
                raise WclError(f"Warcraft Logs refused the client credentials ({status_text(response.status)}): {response.body}")

            try:
                issued = json.loads(response.body)
                value, expires_in = str(issued["access_token"]), float(issued["expires_in"])
            except (ValueError, KeyError, TypeError) as e:
                raise WclError(f"error decoding response body: {e}") from None
            self._token = _AccessToken(value, time.monotonic() + expires_in)
            return value


def _chunks(items: list[int], size: int) -> Iterable[list[int]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


# The queries take their values as GraphQL variables, never spliced into the text, so a report code typed
# into a command can't change what is asked. The one thing built into a query is the list of ability
# IDs, which are numbers.
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

# Every death in one pull, with no cutoff: the replay needs to see the wipe itself.
ALL_DEATHS_QUERY = "query($code: String!, $fightIDs: [Int]) { reportData { report(code: $code) { table(dataType: Deaths, fightIDs: $fightIDs) } } }"


def alias(index: int) -> str:
    """The alias of the table for the ability at `index` in an abilities query."""
    return f"a{index}"


def ability_tables_query(data_type: str, ability_ids: Sequence[int], cut_off: bool) -> str:
    """One table of `data_type` per ability, each filtered to that ability and so listing the players who
    have it (took the damage, or cast it). `cut_off` asks for the cutoff to be applied. (Not `viewBy:
    Ability`, whose per-ability player lists hold only the top five.)"""
    cut = f", wipeCutoff: {WIPE_CUTOFF}" if cut_off else ""
    tables = "".join(
        f" {alias(index)}: table(dataType: {data_type}, fightIDs: $fightIDs, abilityID: {int(ability_id)}{cut})" for index, ability_id in enumerate(ability_ids)
    )
    return f"query($code: String!, $fightIDs: [Int]) {{ reportData {{ report(code: $code) {{{tables} }} }} }}"


def parse_response(body: Any) -> dict[str, Any]:
    """The data of a response, or the API's error messages if it sent any (even alongside partial data)."""
    if not isinstance(body, dict):
        raise WclError("Warcraft Logs sent no data.")
    errors = body.get("errors")
    if errors:
        messages = [str(error.get("message")) if isinstance(error, dict) else str(error) for error in errors]
        raise WclError(f"Warcraft Logs reported: {'; '.join(messages)}")
    data = body.get("data")
    if data is None:
        raise WclError("Warcraft Logs sent no data.")
    return data


@dataclass(frozen=True)
class Fight:
    id: int
    name: str
    kill: bool | None
    in_progress: bool | None
    # Milliseconds since the start of the report.
    start_time: float = 0.0
    # Milliseconds since the start of the report.
    end_time: float = 0.0

    @property
    def is_finished(self) -> bool:
        """A pull that is still being uploaded is left out everywhere: its events aren't all there yet."""
        return not bool(self.in_progress)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Fight:
        return cls(
            id=int(data["id"]),
            name=str(data["name"]),
            kill=data.get("kill"),
            in_progress=data.get("inProgress"),
            start_time=float(data.get("startTime") or 0.0),
            end_time=float(data.get("endTime") or 0.0),
        )


@dataclass(frozen=True)
class Report:
    code: str
    title: str
    # Milliseconds since the Unix epoch.
    start_time: float
    fights: list[Fight]

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Report:
        try:
            return cls(
                code=str(data["code"]),
                title=str(data["title"]),
                start_time=float(data["startTime"]),
                fights=[Fight.from_json(fight) for fight in data.get("fights") or []],
            )
        except (KeyError, TypeError, ValueError) as e:
            raise WclError(f"Warcraft Logs sent something unexpected: {e}") from None


def select_fights(fights: Sequence[Fight], boss: str | None) -> list[Fight]:
    """The finished fights of `fights`, narrowed to those of the boss whose name contains `boss`
    (ignoring case) when one is given. Raises with which bosses the report does have."""
    finished = [fight for fight in fights if fight.is_finished]
    boss = None if boss is None else boss.strip()
    if not boss:
        return finished

    needle = boss.lower()
    matching = [fight for fight in finished if needle in fight.name.lower()]
    if matching:
        return matching

    names = sorted({fight.name for fight in finished})
    if not names:
        raise WclError("This report has no Mythic boss pulls.")
    raise WclError(f"No Mythic pulls of a boss matching `{boss}`. The bosses in this report: {', '.join(names)}.")


@dataclass(frozen=True)
class ReportSummary:
    code: str
    # Milliseconds since the Unix epoch.
    start_time: float
    owner_id: int | None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ReportSummary:
        owner = data.get("owner")
        return cls(str(data["code"]), float(data["startTime"]), int(owner["id"]) if isinstance(owner, dict) else None)

    def started_at_or_after(self, moment: datetime) -> bool:
        millis = int(moment.timestamp()) * 1000 + moment.microsecond // 1000
        return self.start_time >= millis


def newest_by_uploader(reports: Sequence[ReportSummary], uploader_id: int) -> ReportSummary | None:
    """The newest of `reports` that `uploader_id` uploaded."""
    theirs = [report for report in reports if report.owner_id == uploader_id]
    return max(theirs, key=lambda report: report.start_time, default=None)


@dataclass(frozen=True)
class DeathEntry:
    """One death in one pull. Only the fields the overview uses are read."""

    name: str
    # The class, written without spaces ("DeathKnight").
    class_name: str
    fight: int
    # Milliseconds since the start of the report.
    timestamp: float


def _deaths_from(data: dict[str, Any]) -> list[DeathEntry]:
    report = (data.get("reportData") or {}).get("report")
    if report is None:
        raise WclError(NOT_PUBLIC)
    try:
        entries = report["table"]["data"]["entries"]
        return [DeathEntry(str(entry["name"]), str(entry.get("type") or ""), int(entry["fight"]), float(entry["timestamp"])) for entry in entries]
    except (KeyError, TypeError, ValueError) as e:
        raise WclError(f"Warcraft Logs sent something unexpected: {e}") from None


@dataclass(frozen=True)
class PlayerEntry:
    """One player's total for one ability: damage taken from it, or how often they cast it."""

    name: str
    class_name: str
    total: float


def _player_entries(table: Any) -> list[PlayerEntry]:
    try:
        return [PlayerEntry(str(entry["name"]), str(entry.get("type") or ""), float(entry["total"])) for entry in table["data"]["entries"]]
    except (KeyError, TypeError, ValueError) as e:
        raise WclError(f"Warcraft Logs sent something unexpected: {e}") from None


def ignored_set(names: Iterable[str]) -> set[str]:
    """Player names in the form they are compared in: lowercase, so capitalisation never matters."""
    return {name.lower() for name in names}


def is_ignored(ignored: set[str], name: str) -> bool:
    return name.lower() in ignored


def add_totals(players: dict[str, tuple[str, int]], entries: Iterable[PlayerEntry], ignored: set[str]) -> None:
    """Adds `entries`, except those of ignored players, to the running totals per player. Pulls are
    queried in chunks, and a player's total over the night is the sum over the chunks."""
    for entry in entries:
        if is_ignored(ignored, entry.name):
            continue
        class_name, total = players.get(entry.name, (entry.class_name, 0))
        players[entry.name] = (class_name, total + int(max(entry.total, 0.0)))


@dataclass(frozen=True)
class EncounterSummary:
    name: str
    pulls: int
    kills: int


@dataclass(frozen=True)
class PlayerCount:
    name: str
    class_name: str
    count: int


@dataclass(frozen=True)
class PlayerTotal:
    name: str
    class_name: str
    total: int


@dataclass(frozen=True)
class AbilityDamage:
    """The damage taken from one ability: in all, and by each player who took any."""

    name: str
    total: int
    # Most damage first (ties in name order).
    players: list[PlayerTotal]

    @classmethod
    def build(cls, ability: TrackedAbility, players: dict[str, tuple[str, int]]) -> AbilityDamage:
        ranking = ranked_players(players)
        return cls(ability.name, sum(player.total for player in ranking), ranking)


def ranked_players(players: dict[str, tuple[str, int]]) -> list[PlayerTotal]:
    """Players with their totals, most first (ties in name order)."""
    ranking = [PlayerTotal(name, class_name, total) for name, (class_name, total) in players.items()]
    ranking.sort(key=lambda player: (-player.total, player.name))
    return ranking


def _started_at(report: Report) -> int:
    """The start of the report as Unix seconds."""
    return int(report.start_time / 1000.0)


@dataclass(frozen=True)
class Overview:
    """What `/wcl report` shows for one report."""

    code: str
    title: str
    # Unix timestamp, in seconds, of the start of the report.
    started_at: int
    encounters: list[EncounterSummary]
    # Players ranked by how many of their deaths counted.
    deaths: list[PlayerCount]
    # Players ranked by how many pulls they were the first to die in.
    first_deaths: list[PlayerCount]
    # Damage taken from each tracked ability.
    damage: list[AbilityDamage]
    # How many players were left out of all of it, for the footer.
    ignored: int

    @classmethod
    def build(cls, report: Report, deaths: Sequence[DeathEntry], damage: list[AbilityDamage], ignored: int) -> Overview:
        counts: dict[str, list[int]] = {}
        order: list[str] = []
        for fight in report.fights:
            if not fight.is_finished:
                continue
            if fight.name not in counts:
                counts[fight.name] = [0, 0]
                order.append(fight.name)
            counts[fight.name][0] += 1
            if fight.kill:
                counts[fight.name][1] += 1

        return cls(
            code=report.code,
            title=report.title,
            started_at=_started_at(report),
            encounters=[EncounterSummary(name, counts[name][0], counts[name][1]) for name in order],
            deaths=tally(deaths),
            first_deaths=tally(first_death_of_each_pull(deaths)),
            damage=damage,
            ignored=ignored,
        )


@dataclass(frozen=True)
class DamageReport:
    """What `/wcl damage_taken` shows: one ability's damage over some of a report's pulls."""

    code: str
    title: str
    started_at: int
    # Which bosses the pulls are of.
    scope: str
    pulls: int
    ability: AbilityDamage
    # How many players were left out, for the footer.
    ignored: int

    @classmethod
    def build(cls, report: Report, fights: Sequence[Fight], ability: AbilityDamage, boss: str | None, ignored: int) -> DamageReport:
        return cls(report.code, report.title, _started_at(report), scope_text(fights, boss), len(fights), ability, ignored)


def scope_text(fights: Sequence[Fight], boss: str | None) -> str:
    """Which bosses a set of pulls is of, for an embed: the boss names when the pulls were narrowed to a
    boss, otherwise all of them."""
    if boss is not None and boss.strip():
        # Repeats next to each other are dropped, as the pulls of one boss come together.
        names: list[str] = []
        for fight in fights:
            if not names or names[-1] != fight.name:
                names.append(fight.name)
        return ", ".join(names)
    return "All Mythic bosses"


@dataclass(frozen=True)
class ConsumableUse:
    """The uses of one kind of consumable: in all, and by each player who used any."""

    kind: Kind
    # Whether any ability is registered for this kind; if not, there is nothing to count.
    registered: bool
    total: int
    # Most uses first (ties in name order).
    players: list[PlayerTotal]


@dataclass(frozen=True)
class ConsumablesReport:
    """What `/wcl consumables` shows: how many healthstones and health potions were used."""

    code: str
    title: str
    started_at: int
    scope: str
    pulls: int
    # One entry for each kind, healthstones first.
    uses: list[ConsumableUse]
    ignored: int

    @classmethod
    def build(
        cls,
        report: Report,
        fights: Sequence[Fight],
        boss: str | None,
        registered: Sequence[Registered],
        totals: Sequence[dict[str, tuple[str, int]]],
        ignored: int,
    ) -> ConsumablesReport:
        """`totals` holds each registered ability's players, in the order of `registered`; the abilities
        of one kind are added together (a plain and a demonic healthstone are both healthstones)."""
        uses = []
        for kind in ALL_KINDS:
            players: dict[str, tuple[str, int]] = {}
            any_registered = False
            for entry, per_ability in zip(registered, totals, strict=False):
                if entry.kind is not kind:
                    continue
                any_registered = True
                for name, (class_name, total) in per_ability.items():
                    held_class, held_total = players.get(name, (class_name, 0))
                    players[name] = (held_class, held_total + total)
            ranking = ranked_players(players)
            uses.append(ConsumableUse(kind, any_registered, sum(player.total for player in ranking), ranking))

        return cls(report.code, report.title, _started_at(report), scope_text(fights, boss), len(fights), uses, ignored)


def tally(deaths: Iterable[DeathEntry]) -> list[PlayerCount]:
    """Counts deaths per player, most first (ties in name order)."""
    counts: dict[str, PlayerCount] = {}
    for death in deaths:
        held = counts.get(death.name)
        counts[death.name] = PlayerCount(death.name, held.class_name if held else death.class_name, (held.count if held else 0) + 1)
    return sorted(counts.values(), key=lambda player: (-player.count, player.name))


def first_death_of_each_pull(deaths: Sequence[DeathEntry]) -> list[DeathEntry]:
    """The earliest death in each pull. Deaths at the very same moment keep the order they were listed in."""
    first: dict[int, DeathEntry] = {}
    for death in deaths:
        earlier = first.get(death.fight)
        if earlier is None or death.timestamp < earlier.timestamp:
            first[death.fight] = death
    return list(first.values())


def report_url(code: str) -> str:
    return f"https://www.warcraftlogs.com/reports/{code}"


def parse_report_code(text: str) -> str | None:
    """The report code in what an officer typed: a bare code, or a link such as
    `https://www.warcraftlogs.com/reports/YRwPxnJ4MKpWFyrg#fight=3`."""
    text = text.strip()
    _, found, rest = text.partition("/reports/")
    after_path = rest if found else text
    code = after_path
    for separator in "/?#":
        code = code.split(separator, 1)[0]
    if code and len(code) <= 32 and all(char.isascii() and char.isalnum() for char in code):
        return code
    return None


def footer_text(ignored: int) -> str:
    """The footer of the overview and damage embeds: what is counted, and how many players are left out."""
    return f"Only the first {WIPE_CUTOFF} deaths in each pull count, and damage taken up to the last of them.{ignored_note(ignored)}"


def consumables_footer(ignored: int) -> str:
    """The footer of the consumables embed."""
    return f"Every use in every Mythic pull counts, including once the raid had started wiping.{ignored_note(ignored)}"


def ignored_note(ignored: int) -> str:
    """A sentence for a footer saying how many players are left out, or nothing if none are."""
    if ignored == 0:
        return ""
    if ignored == 1:
        return " 1 ignored player is left out."
    return f" {ignored} ignored players are left out."


def embed_chars(title: str, description: str, footer: str, fields: Sequence[tuple[str, str]]) -> int:
    """The characters of an embed that count towards Discord's limit on its total size."""
    return len(title) + len(description) + len(footer) + sum(len(name) + len(value) for name, value in fields)


@dataclass(frozen=True)
class LastWipe:
    """The last wipe of a report, and the moment a replay of it should start at."""

    code: str
    fight_id: int
    boss: str
    # Which pull of this boss it was, counting from 1.
    pull: int
    # Milliseconds since the start of the pull: where the replay starts (the link's `position`).
    position_ms: float
    # Whether `WIPE_DEATHS` players really died within `WIPE_WINDOW_MS`. If not, the replay starts
    # before the end of the pull instead.
    wipe_found: bool

    @classmethod
    def build(cls, code: str, fight: Fight, pull: int, death_times: Sequence[float]) -> LastWipe:
        moment = wipe_moment(death_times)
        wipe_found = moment is not None
        if moment is None:
            # Nobody went down together: the pull most likely ended some other way, so the end of the
            # pull is the best place to look.
            moment = max([fight.end_time, *death_times])
        position_ms = max(moment - REPLAY_LEAD_IN_MS, fight.start_time) - fight.start_time
        return cls(code, fight.id, fight.name, pull, position_ms, wipe_found)


def wipe_moment(death_times: Sequence[float]) -> float | None:
    """The time of the first death in the first run of `WIPE_DEATHS` deaths that all fall within
    `WIPE_WINDOW_MS`. `None` if there is no such run."""
    times = sorted(death_times)
    for start in range(len(times) - WIPE_DEATHS + 1):
        if times[start + WIPE_DEATHS - 1] - times[start] <= WIPE_WINDOW_MS:
            return times[start]
    return None


def replay_url(wipe: LastWipe) -> str:
    """The link to the replay of a pull, opened `position_ms` into the pull."""
    # Rounded half away from zero, as the Rust bot did.
    position = math.floor(wipe.position_ms + 0.5)
    return f"{report_url(wipe.code)}?fight={wipe.fight_id}&view=replay&position={position}"


def _embed(title: str | None = None, url: str | None = None, description: str | None = None, footer: str | None = None) -> dict[str, Any]:
    embed: dict[str, Any] = {"type": "rich", "color": EMBED_COLOUR}
    if title is not None:
        embed["title"] = title
    if url is not None:
        embed["url"] = url
    if description is not None:
        embed["description"] = description
    if footer is not None:
        embed["footer"] = {"text": footer}
    return embed


def _add_field(embed: dict[str, Any], name: str, value: str, inline: bool = False) -> None:
    embed.setdefault("fields", []).append({"name": name, "value": value, "inline": inline})


def replay_embed(wipe: LastWipe) -> dict[str, Any]:
    minutes = math.floor(wipe.position_ms / 60_000.0)
    seconds = math.floor((wipe.position_ms / 1000.0) % 60.0)
    if wipe.wipe_found:
        how = f"Starts 5 seconds before {WIPE_DEATHS} players died within {WIPE_WINDOW_MS / 1000.0:g} seconds."
    else:
        how = "Nobody died together, so it starts near the end of the pull."
    return _embed(
        title=truncate(f"{wipe.boss} — pull {wipe.pull}, wipe", MAX_TITLE_CHARS),
        url=replay_url(wipe),
        description=f"[Open the replay]({replay_url(wipe)})\n{how} That is {minutes}:{seconds:02} into the pull.",
    )


def overview_embed(overview: Overview) -> dict[str, Any]:
    title = truncate(overview.title, MAX_TITLE_CHARS)
    description = overview_description(overview)

    fields: list[tuple[str, str]] = []
    if overview.deaths:
        fields.append(("Most deaths", ranked_list(death_rows(overview.deaths), MOST_DEATHS_SHOWN, MAX_FIELD_CHARS)))
        fields.append(("First to die in a pull", ranked_list(death_rows(overview.first_deaths), FIRST_DEATHS_SHOWN, MAX_FIELD_CHARS)))

    # An embed holds at most 6000 characters in all, so tracked abilities are added while they fit.
    used = embed_chars(title, description, footer_text(overview.ignored), fields) + FOOTER_NOTE_RESERVE
    left_out = 0
    pulls = sum(encounter.pulls for encounter in overview.encounters)
    for ability in overview.damage:
        name, value = damage_field(ability, DAMAGE_SHOWN_IN_OVERVIEW, pulls)
        size = len(name) + len(value)
        if used + size > MAX_EMBED_CHARS:
            left_out += 1
            continue
        used += size
        fields.append((name, value))

    footer = footer_text(overview.ignored)
    if left_out > 0:
        footer = f"{footer} {left_out} more tracked abilities didn't fit; see /wcl damage_taken."

    embed = _embed(title=title, url=report_url(overview.code), description=description, footer=footer)
    for name, value in fields:
        _add_field(embed, name, value)
    return embed


def damage_field(ability: AbilityDamage, shown: int, pulls: int) -> tuple[str, str]:
    """The field for one tracked ability: its title, the total damage taken from it over all `pulls` of
    the night, and the players who took the most of it."""
    name = f"{truncate(ability.name, MAX_ABILITY_NAME_CHARS)} — damage taken"
    if not ability.players:
        return name, "Nobody took damage from this. If that's unexpected, check its ability ID."
    total = f"Total: **{format_damage(ability.total)}** over {plural(pulls, 'pull')}\n"
    return name, total + ranked_list(damage_rows(ability), shown, MAX_FIELD_CHARS - _blen(total))


def plural(count: int, noun: str) -> str:
    """ "1 pull", "34 pulls"."""
    return f"1 {noun}" if count == 1 else f"{count} {noun}s"


def damage_embed(report: DamageReport) -> dict[str, Any]:
    ability = report.ability
    report_title = truncate(report.title, MAX_ABILITY_NAME_CHARS)
    embed = _embed(
        title=f"{truncate(ability.name, MAX_ABILITY_NAME_CHARS)} — damage taken",
        url=report_url(report.code),
        footer=footer_text(report.ignored),
    )
    if report.pulls == 0:
        embed["description"] = f"<t:{report.started_at}:F> · {report_title}\nNo Mythic boss pulls in this report."
        return embed

    description = (
        f"<t:{report.started_at}:F> · {report_title}\n**{report.scope}**, {report.pulls} pulls\nTotal damage taken: **{format_damage(ability.total)}**"
    )
    # Cut so that however many bosses the scope names, the description stays within Discord's limit.
    embed["description"] = truncate(description, MAX_DESCRIPTION_CHARS)

    if not ability.players:
        _add_field(
            embed,
            "Who took it",
            "Nobody took damage from this in those pulls. If that's unexpected, check the ability ID: Warcraft Logs "
            "can log an ability's damage under a different ID than its spell page shows.",
        )
        return embed
    _add_field(embed, "Who took it", ranked_list(damage_rows(ability), DAMAGE_SHOWN, MAX_FIELD_CHARS))
    return embed


def consumables_embed(report: ConsumablesReport) -> dict[str, Any]:
    embed = _embed(title="Consumables used", url=report_url(report.code), footer=consumables_footer(report.ignored))
    report_title = truncate(report.title, MAX_ABILITY_NAME_CHARS)
    if report.pulls == 0:
        embed["description"] = f"<t:{report.started_at}:F> · {report_title}\nNo Mythic boss pulls in this report."
        return embed

    # Cut so that however many bosses the scope names, the description stays within Discord's limit.
    description = f"<t:{report.started_at}:F> · {report_title}\n**{report.scope}**, {plural(report.pulls, 'pull')}"
    embed["description"] = truncate(description, MAX_DESCRIPTION_CHARS)
    for used in report.uses:
        _add_field(embed, *consumable_field(used))
    return embed


def consumable_field(used: ConsumableUse) -> tuple[str, str]:
    """The field for one kind of consumable: how many were used in all, and by whom. Always has a value,
    since Discord turns down an empty one."""
    if not used.registered:
        return used.kind.plural, "None registered. Add one with `/settings wcl consumable_add`."
    name = f"{used.kind.plural} — {used.total} used"
    if not used.players:
        return name, "None used."
    return name, ranked_list(count_rows(used.players), CONSUMABLE_PLAYERS_SHOWN, MAX_FIELD_CHARS)


def overview_description(overview: Overview) -> str:
    pulls = sum(encounter.pulls for encounter in overview.encounters)
    if pulls == 0:
        return f"<t:{overview.started_at}:F>\nNo Mythic boss pulls in this report."

    kills = sum(encounter.kills for encounter in overview.encounters)
    description = f"<t:{overview.started_at}:F>\n**{pulls}** Mythic pulls: {kills} kills, {pulls - kills} wipes\n"
    for listed, encounter in enumerate(overview.encounters):
        line = f"\n• {encounter.name} — {encounter.pulls} pulls, {encounter.kills} kills"
        overflow_note = f"\n…and {len(overview.encounters) - listed} more."
        if _blen(description) + _blen(line) + _blen(overflow_note) > MAX_DESCRIPTION_CHARS:
            description += overflow_note
            break
        description += line
    return description


@dataclass(frozen=True)
class Row:
    """One line of a ranking: a player, their class, and what is being counted."""

    name: str
    class_name: str
    value: str


def death_rows(players: Sequence[PlayerCount]) -> list[Row]:
    return [Row(player.name, player.class_name, str(player.count)) for player in players]


def count_rows(players: Sequence[PlayerTotal]) -> list[Row]:
    """Each player's count, for a ranking of how many they used."""
    return [Row(player.name, player.class_name, str(player.total)) for player in players]


def damage_rows(ability: AbilityDamage) -> list[Row]:
    """Each player's damage and their share of everything taken from the ability."""
    rows = []
    for player in ability.players:
        share = 0.0 if ability.total == 0 else player.total * 100.0 / ability.total
        rows.append(Row(player.name, player.class_name, f"{format_damage(player.total)} ({share:.1f}%)"))
    return rows


def ranked_list(rows: Sequence[Row], max_lines: int, max_chars: int) -> str:
    """The top `max_lines` of a ranking, one numbered line each, in at most `max_chars`. If players are
    left off, the list ends with a line saying how many."""
    text = ""
    for index, row in enumerate(rows):
        class_name = f" ({display_class(row.class_name)})" if row.class_name else ""
        line = f"{index + 1}. **{row.name}**{class_name} — {row.value}"
        separator = "" if index == 0 else "\n"
        overflow_note = f"\n…and {len(rows) - index} more."
        if index >= max_lines or _blen(text) + _blen(separator) + _blen(line) + _blen(overflow_note) > max_chars:
            text += overflow_note
            break
        text += separator + line
    return text


def format_damage(amount: int) -> str:
    """Damage as a short number: 3.5B, 200.3M, 12.3K."""
    if amount >= 1_000_000_000:
        return f"{amount / 1e9:.1f}B"
    if amount >= 1_000_000:
        return f"{amount / 1e6:.1f}M"
    if amount >= 1_000:
        return f"{amount / 1e3:.1f}K"
    return str(amount)


def display_class(class_name: str) -> str:
    """ "DeathKnight" as "Death Knight"."""
    return "".join(f" {char}" if index > 0 and char.isupper() else char for index, char in enumerate(class_name))


def truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


# --- What an officer asked for ---

_SPELL_ID = re.compile(r"\+?[0-9]+")


def resolve_ability(tracked: list[TrackedAbility], text: str) -> TrackedAbility | None:
    """What an officer typed for an ability: a tracked ability's name (ignoring case), or a spell ID, which
    needn't be tracked (it is then shown as "Ability <id>")."""
    text = text.strip()
    for ability in tracked:
        if ascii_lower(ability.name) == ascii_lower(text):
            return ability

    if not _SPELL_ID.fullmatch(text) or not text.isascii():
        return None
    ability_id = int(text)
    if not 0 < ability_id <= 0xFFFFFFFF:
        return None
    return next((ability for ability in tracked if ability.ability_id == ability_id), TrackedAbility(ability_id, f"Ability {ability_id}"))


@dataclass(frozen=True)
class Code:
    code: str


@dataclass(frozen=True)
class Newest:
    """The newest report from the registered uploader."""

    source: WclSource


Target = Code | Newest


def parse_target(report: str | None, settings: GuildSettings) -> Target:
    """The report an officer asked for. Raises `ValueError` saying why it can't be worked out."""
    if report is not None:
        code = parse_report_code(report)
        if code is None:
            raise ValueError(f"Couldn't find a report code in `{report.strip()}`. Give a report link or its code.")
        return Code(code)
    if settings.wcl_source is None:
        raise ValueError("Give a report link or code, or register the uploader with `/settings wcl source` to use the newest report.")
    return Newest(settings.wcl_source)
