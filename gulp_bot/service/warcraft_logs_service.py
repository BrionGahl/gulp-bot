"""The Warcraft Logs overviews: asks the API for what each needs, and builds it."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime

from ..client.warcraft_logs_client import WarcraftLogsClient, WarcraftLogsClientPool
from ..exception.warcraft_logs_exception import WarcraftLogsError
from ..schema.guild_settings_schema import WclCredentials, WclSource
from ..schema.wcl_report_schema import (
    AbilityDamage,
    ConsumablesReport,
    DamageReport,
    LastWipe,
    PlayerTotals,
    ReportCode,
    ReportOverview,
    ReportTarget,
)
from ..schema.wcl_settings_schema import ConsumableAbility, TrackedAbility
from .warcraft_logs_analysis_service import (
    build_ability_damage,
    build_consumables_report,
    build_damage_report,
    build_last_wipe,
    build_overview,
    ignored_set,
    is_ignored,
    last_wipe_of,
    newest_by_uploader,
    select_fights,
    totals_of,
)
from .wcl_ignored_player_service import IgnoredPlayerService
from .wcl_tracked_ability_service import TrackedAbilityService

log = logging.getLogger(__name__)


class WarcraftLogsService:
    def __init__(self, clients: WarcraftLogsClientPool, tracked_abilities: TrackedAbilityService, ignored_players: IgnoredPlayerService):
        self._clients = clients
        self._tracked_abilities = tracked_abilities
        self._ignored_players = ignored_players

    def client_for(self, guild_id: int, credentials: WclCredentials) -> WarcraftLogsClient:
        """The guild's API client, kept between commands so that its access token is reused."""
        return self._clients.client_for(guild_id, credentials)

    async def newest_report_code(self, client: WarcraftLogsClient, source: WclSource, started_since: datetime | None = None) -> str | None:
        """The newest report in the guild's recent uploads that came from the registered uploader, as its
        code, or `None` if there isn't one among the guild's newest reports. With `started_since`, only a
        report started at or after then counts: for telling one raid night's report from the night
        before's."""
        newest = newest_by_uploader(await client.fetch_guild_reports(source.guild_id), source.uploader_id)
        if newest is None or (started_since is not None and not newest.started_at_or_after(started_since)):
            return None
        return newest.code

    async def report_code_for(self, client: WarcraftLogsClient, target: ReportTarget) -> str | None:
        """The code of the report asked for, looking up the newest one if that is what was asked."""
        if isinstance(target, ReportCode):
            return target.code
        return await self.newest_report_code(client, target.source)

    async def overview_for_guild(self, guild_id: int, client: WarcraftLogsClient, code: str) -> ReportOverview:
        """`overview`, with the abilities the guild tracks and the players it ignores."""
        tracked = await self._tracked_abilities.list_tracked(guild_id)
        ignored = await self._ignored_players.list_ignored(guild_id)
        return await self.overview(client, code, tracked, ignored)

    async def overview(self, client: WarcraftLogsClient, code: str, tracked: Sequence[TrackedAbility], ignored: Sequence[str]) -> ReportOverview:
        """Everything the overview shows for one report: its Mythic boss pulls, the deaths in them with the
        cutoff applied, and the damage taken from each of the `tracked` abilities.

        Players named in `ignored` are left out of all of it: they are in no ranking, and what they took
        doesn't count towards any total. (The cutoff is the API's, so an ignored player's death among the
        first two in a pull still uses up one of them.)"""
        report = await client.fetch_report(code)
        fight_ids = [fight.id for fight in report.fights if fight.is_finished]
        ignored_names = ignored_set(ignored)

        deaths = [death for death in await client.fetch_counted_deaths(code, fight_ids) if not is_ignored(ignored_names, death.name)]
        damage = await self._damage_taken(client, code, fight_ids, tracked, ignored_names)

        log.info("Fetched Warcraft Logs report %s: %d Mythic pulls, %d counted deaths, %d tracked abilities", code, len(fight_ids), len(deaths), len(tracked))
        return build_overview(report, deaths, damage, len(ignored))

    async def damage_report(self, client: WarcraftLogsClient, code: str, boss: str | None, ability: TrackedAbility, ignored: Sequence[str]) -> DamageReport:
        """Who took damage from one ability, over the pulls of the boss whose name contains `boss`, or of
        every Mythic boss when there is none. Players named in `ignored` are left out, as in `overview`."""
        report = await client.fetch_report(code)
        fights = select_fights(report.fights, boss)
        damage = await self._damage_taken(client, code, [fight.id for fight in fights], [ability], ignored_set(ignored))
        if not damage:
            raise WarcraftLogsError("Warcraft Logs sent no damage data.")

        log.info("Fetched damage taken from %s in Warcraft Logs report %s: %d pulls", ability.name, code, len(fights))
        return build_damage_report(report, fights, damage[-1], boss, len(ignored))

    async def consumables_report(
        self, client: WarcraftLogsClient, code: str, boss: str | None, registered: Sequence[ConsumableAbility], ignored: Sequence[str]
    ) -> ConsumablesReport:
        """How many healthstones and health potions were used, over the pulls of the boss whose name
        contains `boss` or of every Mythic boss when there is none. Every use counts, not only those before
        the cutoff: this is about how much the raid consumed. Players named in `ignored` are left out."""
        report = await client.fetch_report(code)
        fights = select_fights(report.fights, boss)
        tables = await client.fetch_ability_tables(code, [fight.id for fight in fights], "Casts", [entry.ability_id for entry in registered], cut_off=False)
        names = ignored_set(ignored)
        totals: list[PlayerTotals] = [totals_of(entries, names) for entries in tables]

        log.info("Fetched consumables in Warcraft Logs report %s: %d pulls, %d registered abilities", code, len(fights), len(registered))
        return build_consumables_report(report, fights, boss, registered, totals, len(ignored))

    async def last_wipe(self, client: WarcraftLogsClient, code: str) -> LastWipe:
        """The last Mythic boss pull of the report that was a wipe, and where in it to start a replay. Pulls
        still being uploaded are skipped."""
        report = await client.fetch_report(code)
        fight, pull = last_wipe_of(report)
        times = [death.timestamp for death in await client.fetch_all_deaths(code, fight.id) if death.fight == fight.id]

        log.info("Fetched the last wipe of Warcraft Logs report %s: fight %d, %d deaths", code, fight.id, len(times))
        return build_last_wipe(code, fight, pull, times)

    async def _damage_taken(
        self, client: WarcraftLogsClient, code: str, fight_ids: list[int], abilities: Sequence[TrackedAbility], ignored: set[str]
    ) -> list[AbilityDamage]:
        """The damage each player took from each of `abilities` in `fight_ids`, up to the cutoff in every
        pull, in the order of `abilities`."""
        tables = await client.fetch_ability_tables(code, fight_ids, "DamageTaken", [ability.ability_id for ability in abilities], cut_off=True)
        return [build_ability_damage(ability, totals_of(entries, ignored)) for ability, entries in zip(abilities, tables, strict=False)]
