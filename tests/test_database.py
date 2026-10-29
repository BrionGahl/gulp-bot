"""The tables, against a real Postgres: `TEST_DATABASE_URL=postgres://... pytest tests/test_database.py`.

Use a throwaway database: the tests empty the tables they touch.
"""

import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest

asyncpg = pytest.importorskip("asyncpg")
URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(URL is None, reason="needs TEST_DATABASE_URL pointing at a disposable Postgres database")

from gulp_bot.core import database  # noqa: E402
from gulp_bot.core.cron import Schedule  # noqa: E402
from gulp_bot.core.migrations import run_migrations  # noqa: E402
from gulp_bot.repository.announcement_repository import AnnouncementRepository  # noqa: E402
from gulp_bot.repository.guild_settings_repository import GuildSettingsRepository  # noqa: E402
from gulp_bot.repository.officer_channel_repository import OfficerChannelRepository  # noqa: E402
from gulp_bot.repository.raid_attendance_repository import RaidAttendanceRepository  # noqa: E402
from gulp_bot.repository.wcl_consumable_ability_repository import ConsumableAbilityRepository  # noqa: E402
from gulp_bot.repository.wcl_ignored_player_repository import IgnoredPlayerRepository  # noqa: E402
from gulp_bot.repository.wcl_tracked_ability_repository import TrackedAbilityRepository  # noqa: E402
from gulp_bot.repository.wcl_tracked_enemy_repository import TrackedEnemyRepository  # noqa: E402
from gulp_bot.schema.announcement_schema import NewAnnouncement  # noqa: E402
from gulp_bot.schema.guild_settings_schema import (  # noqa: E402
    AiSettings,
    GuildIdSetting,
    RaidVoice,
    ThinkSetting,
    WclCredentials,
    WclSource,
    WowUtilsCredentials,
)
from gulp_bot.schema.officer_channel_schema import RegisteredOfficerChannel  # noqa: E402
from gulp_bot.schema.wcl_settings_schema import (  # noqa: E402
    ConsumableKind,
    IgnorePlayerOutcome,
    RegisterConsumableOutcome,
    TrackAbilityOutcome,
    TrackEnemyOutcome,
)
from gulp_bot.service import wcl_consumable_service, wcl_ignored_player_service, wcl_tracked_ability_service, wcl_tracked_enemy_service  # noqa: E402
from gulp_bot.service.guild_settings_service import GuildSettingsService  # noqa: E402
from gulp_bot.service.wcl_consumable_service import ConsumableService, ids_of_kind  # noqa: E402
from gulp_bot.service.wcl_ignored_player_service import IgnoredPlayerService  # noqa: E402
from gulp_bot.service.wcl_tracked_ability_service import TrackedAbilityService  # noqa: E402
from gulp_bot.service.wcl_tracked_enemy_service import TrackedEnemyService  # noqa: E402

GUILD_A, GUILD_B = 111, 222


def with_pool(test):
    async def run():
        pool = await database.connect(URL)  # type: ignore[arg-type]
        try:
            await run_migrations(pool)
            await test(pool)
        finally:
            await pool.close()

    asyncio.run(run())


def test_migrating_twice_changes_nothing():
    async def test(pool):
        await run_migrations(pool)
        rows = await pool.fetch("SELECT version, success FROM _sqlx_migrations ORDER BY version")
        assert [row["version"] for row in rows] == list(range(1, 17)) and all(row["success"] for row in rows)

    with_pool(test)


def test_settings_round_trip_and_stay_scoped_to_their_guild():
    async def test(pool):
        await pool.execute("TRUNCATE guild_settings, guild_clips_channels, guild_replay_users")
        big_role = 2**64 - 6
        store = await GuildSettingsService.load(GuildSettingsRepository(pool))
        await store.set_id_setting(GUILD_A, GuildIdSetting.MOD_ROLE, big_role)
        await store.set_id_setting(GUILD_A, GuildIdSetting.RAID_NOTES_CHANNEL, 42)
        await store.set_id_setting(GUILD_A, GuildIdSetting.TRIAL_ROLE, 7)
        await store.set_id_setting(GUILD_A, GuildIdSetting.TRIAL_ROLE, None)
        await store.add_replay_user(GUILD_A, 8)
        await store.add_replay_user(GUILD_A, 9)
        await store.add_replay_user(GUILD_A, 9)
        await store.add_replay_user(GUILD_B, 8)
        assert await store.remove_replay_user(GUILD_A, 8)
        assert not await store.remove_replay_user(GUILD_A, 8)
        tuesdays = Schedule.parse("0 30 17 * * Tue *")
        await store.set_raid_notes_schedule(GUILD_A, tuesdays)
        await store.set_raid_notes_schedule(GUILD_B, tuesdays)
        await store.set_raid_notes_schedule(GUILD_B, None)
        raid_voice = RaidVoice(77, Schedule.parse("0 0 20 * * Tue,Thu *"), Schedule.parse("0 0 23 * * Tue,Thu *"))
        await store.set_raid_voice(GUILD_A, raid_voice)
        await store.set_raid_voice(GUILD_B, raid_voice)
        await store.set_raid_voice(GUILD_B, None)
        await store.set_bart_token(GUILD_A, "bart-a")
        await store.set_wowutils(GUILD_A, WowUtilsCredentials("wow-a", "group-a"))
        await store.set_bart_token(GUILD_B, "bart-b")
        await store.set_wowutils(GUILD_B, WowUtilsCredentials("wow-b", "group-b"))
        await store.set_wowutils(GUILD_B, None)
        source = WclSource(811636, 240984)
        await store.set_wcl_source(GUILD_A, source)
        await store.set_wcl_source(GUILD_B, source)
        await store.set_wcl_source(GUILD_B, None)
        credentials = WclCredentials("wcl-id-a", "wcl-secret-a")
        await store.set_wcl_credentials(GUILD_A, credentials)
        await store.set_wcl_credentials(GUILD_B, credentials)
        await store.set_wcl_credentials(GUILD_B, None)
        await store.set_ai(GUILD_A, AiSettings(maintenance=True, model="qwen3:14b", think=ThinkSetting.ON))
        await store.add_clips_channel(GUILD_A, 5)
        await store.add_clips_channel(GUILD_A, 5)
        await store.add_clips_channel(GUILD_B, 6)
        assert await store.remove_clips_channel(GUILD_B, 6)
        assert not await store.remove_clips_channel(GUILD_B, 6)

        # The in-memory copy and a fresh load from the database must agree.
        for loaded in (store, await GuildSettingsService.load(GuildSettingsRepository(pool))):
            a = loaded.get(GUILD_A)
            assert a.mod_role_id == big_role and a.raid_notes_channel_id == 42 and a.trial_role_id is None
            assert a.replay_user_ids == {9} and loaded.get(GUILD_B).replay_user_ids == {8}
            assert a.raider_role_id is None and a.clips_channel_ids == {5}
            assert (a.bart_token, a.wowutils_token, a.wowutils_group_id) == ("bart-a", "wow-a", "group-a")
            assert a.raid_notes_cron is not None and a.raid_notes_cron.source == tuesdays.source
            assert a.raid_voice is not None and a.raid_voice.channel_id == 77 and a.raid_voice.end.source == "0 0 23 * * Tue,Thu *"
            assert a.wcl_source == source and a.wcl_credentials == credentials
            assert a.ai == AiSettings(maintenance=True, model="qwen3:14b", think=ThinkSetting.ON)
            b = loaded.get(GUILD_B)
            assert b.mod_role_id is None and not b.clips_channel_ids and b.bart_token == "bart-b"
            assert b.wowutils_token is None and b.raid_notes_cron is None and b.raid_voice is None
            assert b.wcl_source is None and b.wcl_credentials is None
            assert b.ai == AiSettings()

    with_pool(test)


def test_announcements_are_scoped_to_their_guild():
    async def test(pool):
        await pool.execute("TRUNCATE announcements")
        announcements = AnnouncementRepository(pool)
        schedule = Schedule.parse("0 0 12 * * Tue *")
        message = "@Raider\n\n## Weekly Reset\nMulti-line, with *markdown*."
        first = await announcements.insert(NewAnnouncement(GUILD_A, 10, schedule, message, 1))
        second = await announcements.insert(NewAnnouncement(GUILD_A, 11, schedule, "second", 1))
        other = await announcements.insert(NewAnnouncement(GUILD_B, 20, schedule, "other guild", 2))

        listed = await announcements.list_for_guild(GUILD_A)
        assert [entry.id for entry in listed] == [first, second]
        assert listed[0].message == message and listed[0].schedule.source == schedule.source and listed[0].channel_id == 10
        assert await announcements.count_for_guild(GUILD_A) == 2
        assert await announcements.find(GUILD_A, other) is None
        assert not await announcements.delete(GUILD_A, other)
        assert await announcements.find(GUILD_B, other) is not None
        assert await announcements.delete(GUILD_A, first)
        assert not await announcements.delete(GUILD_A, first)
        assert [entry.id for entry in await announcements.list_all()] == [second, other]

    with_pool(test)


def test_a_member_can_only_be_claimed_once_per_guild():
    async def test(pool):
        await pool.execute("TRUNCATE personal_officer_channels")
        channels = OfficerChannelRepository(pool)
        alice, bob = 1, 2
        claims = await asyncio.gather(*(channels.claim(GUILD_A, alice) for _ in range(3)))
        assert sum(claims) == 1
        assert await channels.claim(GUILD_B, alice)
        await channels.save_channel(GUILD_A, bob, 50)
        assert not await channels.claim(GUILD_A, bob)
        await channels.save_channel(GUILD_A, alice, 40)
        assert await channels.list_for_guild(GUILD_A) == [RegisteredOfficerChannel(alice, 40), RegisteredOfficerChannel(bob, 50)]
        assert await channels.list_for_guild(GUILD_B) == [RegisteredOfficerChannel(alice, None)]
        assert await channels.delete(GUILD_A, bob)
        assert not await channels.delete(GUILD_A, bob)
        assert await channels.claim(GUILD_A, bob)

        # Deleting a member's channel forgets their row, but only in that channel's guild.
        assert not await channels.delete_channel(GUILD_B, 40)
        assert await channels.delete_channel(GUILD_A, 40)
        assert await channels.claim(GUILD_A, alice)

    with_pool(test)


def test_a_start_snapshot_is_stored_per_guild_and_taken_once():
    async def test(pool):
        await pool.execute("TRUNCATE raid_attendance_starts")
        attendance = RaidAttendanceRepository(pool)
        at = datetime(2026, 10, 14, 0, 0, tzinfo=UTC)
        present = [1, 2**64 - 6]
        await attendance.save_start_snapshot(GUILD_A, 8, at, [7])
        await attendance.save_start_snapshot(GUILD_A, 9, at, present)
        await attendance.save_start_snapshot(GUILD_B, 9, at, [])
        taken = await attendance.take_start_snapshot(GUILD_A)
        assert taken is not None and (taken.voice_channel_id, taken.taken_at, taken.present) == (9, at, present)
        assert taken.pairs_with(9, at + timedelta(hours=3))
        assert await attendance.take_start_snapshot(GUILD_A) is None
        b = await attendance.take_start_snapshot(GUILD_B)
        assert b is not None and b.present == []

    with_pool(test)


def test_tracked_abilities_are_scoped_to_their_guild_and_limited():
    async def test(pool):
        await pool.execute("TRUNCATE wcl_tracked_abilities")
        mechanics = TrackedAbilityService(TrackedAbilityRepository(pool))
        added = TrackAbilityOutcome.ADDED
        assert (await mechanics.track(GUILD_A, 1282408, "Coalesced Venom")).outcome is added
        assert (await mechanics.track(GUILD_A, 1299838, "Venom Rupture")).outcome is added
        assert (await mechanics.track(GUILD_A, 1282408, "coalesced venom (soak)")).outcome is TrackAbilityOutcome.RENAMED
        assert (await mechanics.track(GUILD_A, 5, "VENOM RUPTURE")).other_ability_id == 1299838
        assert (await mechanics.track(GUILD_B, 1282408, "Venom Rupture")).outcome is added
        assert [ability.name for ability in await mechanics.list_tracked(GUILD_A)] == ["coalesced venom (soak)", "Venom Rupture"]
        assert not await mechanics.untrack(GUILD_B, "coalesced venom (soak)")
        assert await mechanics.untrack(GUILD_A, "COALESCED VENOM (SOAK)")
        for ability_id in range(100, 100 + wcl_tracked_ability_service.MAX_TRACKED - 1):
            assert (await mechanics.track(GUILD_A, ability_id, f"Ability {ability_id}")).outcome is added
        assert (await mechanics.track(GUILD_A, 999, "One too many")).outcome is TrackAbilityOutcome.FULL
        assert (await mechanics.track(GUILD_A, 100, "Renamed at the limit")).outcome is TrackAbilityOutcome.RENAMED

    with_pool(test)


def test_tracked_enemies_are_scoped_to_their_guild_and_limited():
    async def test(pool):
        await pool.execute("TRUNCATE wcl_tracked_enemies")
        enemies = TrackedEnemyService(TrackedEnemyRepository(pool))
        added, already = TrackEnemyOutcome.ADDED, TrackEnemyOutcome.ALREADY_TRACKED
        assert await enemies.track(GUILD_A, "Sporecaller") is added
        assert await enemies.track(GUILD_A, "Nexus-King Salhadaar") is added
        assert await enemies.track(GUILD_A, "SPORECALLER") is already
        assert await enemies.track(GUILD_B, "Sporecaller") is added
        assert await enemies.list_tracked(GUILD_A) == ["Nexus-King Salhadaar", "Sporecaller"]
        assert not await enemies.untrack(GUILD_A, "Dimensius")
        assert await enemies.untrack(GUILD_A, "sporecaller")
        assert await enemies.list_tracked(GUILD_B) == ["Sporecaller"]
        for n in range(wcl_tracked_enemy_service.MAX_TRACKED - 1):
            assert await enemies.track(GUILD_A, f"Add {n}") is added
        assert await enemies.track(GUILD_A, "One too many") is TrackEnemyOutcome.FULL

    with_pool(test)


def test_ignored_players_are_scoped_to_their_guild_and_limited():
    async def test(pool):
        await pool.execute("TRUNCATE wcl_ignored_players")
        ignored = IgnoredPlayerService(IgnoredPlayerRepository(pool))
        added, already = IgnorePlayerOutcome.ADDED, IgnorePlayerOutcome.ALREADY_IGNORED
        assert await ignored.ignore(GUILD_A, "Tritonian") is added
        assert await ignored.ignore(GUILD_A, "aâyo") is added
        assert await ignored.ignore(GUILD_A, "TRITONIAN") is already
        assert await ignored.ignore(GUILD_A, "AÂYO") is already
        assert await ignored.ignore(GUILD_B, "Tritonian") is added
        assert await ignored.list_ignored(GUILD_A) == ["aâyo", "Tritonian"]
        assert not await ignored.stop_ignoring(GUILD_A, "Graveshrug")
        assert await ignored.stop_ignoring(GUILD_A, "tRiToNiAn")
        assert await ignored.list_ignored(GUILD_B) == ["Tritonian"]
        for n in range(wcl_ignored_player_service.MAX_IGNORED - 1):
            assert await ignored.ignore(GUILD_A, f"Player{n}") is added
        assert await ignored.ignore(GUILD_A, "OneTooMany") is IgnorePlayerOutcome.FULL

    with_pool(test)


def test_consumable_abilities_are_scoped_to_their_guild_and_limited():
    async def test(pool):
        await pool.execute("TRUNCATE wcl_consumable_abilities")
        consumables = ConsumableService(ConsumableAbilityRepository(pool))
        added = RegisterConsumableOutcome.ADDED
        assert (await consumables.register(GUILD_A, ConsumableKind.HEALTHSTONE, 6262)).outcome is added
        assert (await consumables.register(GUILD_A, ConsumableKind.HEALTHSTONE, 452930)).outcome is added
        assert (await consumables.register(GUILD_A, ConsumableKind.HEALTH_POTION, 1234768)).outcome is added
        assert (await consumables.register(GUILD_A, ConsumableKind.HEALTHSTONE, 6262)).outcome is RegisterConsumableOutcome.ALREADY_REGISTERED
        assert (await consumables.register(GUILD_A, ConsumableKind.HEALTH_POTION, 6262)).other_kind is ConsumableKind.HEALTHSTONE
        assert (await consumables.register(GUILD_B, ConsumableKind.HEALTH_POTION, 6262)).outcome is added
        registered = await consumables.list_registered(GUILD_A)
        assert ids_of_kind(registered, ConsumableKind.HEALTHSTONE) == [6262, 452930]
        assert ids_of_kind(registered, ConsumableKind.HEALTH_POTION) == [1234768]
        assert await consumables.unregister(GUILD_B, 452930) is None
        assert await consumables.unregister(GUILD_A, 452930) is ConsumableKind.HEALTHSTONE
        for ability_id in range(100, 100 + wcl_consumable_service.MAX_PER_KIND - 1):
            assert (await consumables.register(GUILD_A, ConsumableKind.HEALTHSTONE, ability_id)).outcome is added
        assert (await consumables.register(GUILD_A, ConsumableKind.HEALTHSTONE, 999)).outcome is RegisterConsumableOutcome.FULL
        assert (await consumables.register(GUILD_A, ConsumableKind.HEALTH_POTION, 999)).outcome is added

    with_pool(test)
