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

from gulp_bot import announcements, db, personal_officer_channels, raid_attendance, wcl_consumables, wcl_ignored_players, wcl_mechanics  # noqa: E402
from gulp_bot.cron import Schedule  # noqa: E402
from gulp_bot.guild_settings import GuildSettingsStore, RaidVoice, Setting, WclCredentials, WclSource  # noqa: E402
from gulp_bot.wcl_consumables import Kind  # noqa: E402

GUILD_A, GUILD_B = 111, 222


def with_pool(test):
    async def run():
        pool = await db.connect(URL)  # type: ignore[arg-type]
        try:
            await db.migrate(pool)
            await test(pool)
        finally:
            await pool.close()

    asyncio.run(run())


def test_migrating_twice_changes_nothing():
    async def test(pool):
        await db.migrate(pool)
        rows = await pool.fetch("SELECT version, success FROM _sqlx_migrations ORDER BY version")
        assert [row["version"] for row in rows] == list(range(1, 15)) and all(row["success"] for row in rows)

    with_pool(test)


def test_settings_round_trip_and_stay_scoped_to_their_guild():
    async def test(pool):
        await pool.execute("TRUNCATE guild_settings, guild_clips_channels, guild_replay_users")
        big_role = 2**64 - 6
        store = await GuildSettingsStore.load(pool)
        await store.set(GUILD_A, Setting.MOD_ROLE, big_role)
        await store.set(GUILD_A, Setting.RAID_NOTES_CHANNEL, 42)
        await store.set(GUILD_A, Setting.TRIAL_ROLE, 7)
        await store.set(GUILD_A, Setting.TRIAL_ROLE, None)
        await store.add_replay_user(GUILD_A, 8)
        await store.add_replay_user(GUILD_A, 9)
        await store.add_replay_user(GUILD_A, 9)
        await store.add_replay_user(GUILD_B, 8)
        assert await store.remove_replay_user(GUILD_A, 8)
        assert not await store.remove_replay_user(GUILD_A, 8)
        tuesdays = Schedule.parse("0 30 17 * * Tue *")
        await store.set_raid_notes_cron(GUILD_A, tuesdays)
        await store.set_raid_notes_cron(GUILD_B, tuesdays)
        await store.set_raid_notes_cron(GUILD_B, None)
        raid_voice = RaidVoice(77, Schedule.parse("0 0 20 * * Tue,Thu *"), Schedule.parse("0 0 23 * * Tue,Thu *"))
        await store.set_raid_voice(GUILD_A, raid_voice)
        await store.set_raid_voice(GUILD_B, raid_voice)
        await store.set_raid_voice(GUILD_B, None)
        await store.set_bart_token(GUILD_A, "bart-a")
        await store.set_wowutils(GUILD_A, ("wow-a", "group-a"))
        await store.set_bart_token(GUILD_B, "bart-b")
        await store.set_wowutils(GUILD_B, ("wow-b", "group-b"))
        await store.set_wowutils(GUILD_B, None)
        source = WclSource(811636, 240984)
        await store.set_wcl_source(GUILD_A, source)
        await store.set_wcl_source(GUILD_B, source)
        await store.set_wcl_source(GUILD_B, None)
        credentials = WclCredentials("wcl-id-a", "wcl-secret-a")
        await store.set_wcl_credentials(GUILD_A, credentials)
        await store.set_wcl_credentials(GUILD_B, credentials)
        await store.set_wcl_credentials(GUILD_B, None)
        await store.add_clips_channel(GUILD_A, 5)
        await store.add_clips_channel(GUILD_A, 5)
        await store.add_clips_channel(GUILD_B, 6)
        assert await store.remove_clips_channel(GUILD_B, 6)
        assert not await store.remove_clips_channel(GUILD_B, 6)

        # The in-memory copy and a fresh load from the database must agree.
        for loaded in (store, await GuildSettingsStore.load(pool)):
            a = loaded.get(GUILD_A)
            assert a.mod_role_id == big_role and a.raid_notes_channel_id == 42 and a.trial_role_id is None
            assert a.replay_user_ids == {9} and loaded.get(GUILD_B).replay_user_ids == {8}
            assert a.raider_role_id is None and a.clips_channel_ids == {5}
            assert (a.bart_token, a.wowutils_token, a.wowutils_group_id) == ("bart-a", "wow-a", "group-a")
            assert a.raid_notes_cron is not None and a.raid_notes_cron.source == tuesdays.source
            assert a.raid_voice is not None and a.raid_voice.channel_id == 77 and a.raid_voice.end.source == "0 0 23 * * Tue,Thu *"
            assert a.wcl_source == source and a.wcl_credentials == credentials
            b = loaded.get(GUILD_B)
            assert b.mod_role_id is None and not b.clips_channel_ids and b.bart_token == "bart-b"
            assert b.wowutils_token is None and b.raid_notes_cron is None and b.raid_voice is None
            assert b.wcl_source is None and b.wcl_credentials is None

    with_pool(test)


def test_announcements_are_scoped_to_their_guild():
    async def test(pool):
        await pool.execute("TRUNCATE announcements")
        schedule = Schedule.parse("0 0 12 * * Tue *")
        message = "@Raider\n\n## Weekly Reset\nMulti-line, with *markdown*."
        first = await announcements.create(pool, GUILD_A, 10, schedule, message, 1)
        second = await announcements.create(pool, GUILD_A, 11, schedule, "second", 1)
        other = await announcements.create(pool, GUILD_B, 20, schedule, "other guild", 2)

        listed = await announcements.list_announcements(pool, GUILD_A)
        assert [entry.id for entry in listed] == [first, second]
        assert listed[0].message == message and listed[0].schedule.source == schedule.source and listed[0].channel_id == 10
        assert await announcements.count(pool, GUILD_A) == 2
        assert await announcements.get(pool, GUILD_A, other) is None
        assert not await announcements.delete(pool, GUILD_A, other)
        assert await announcements.get(pool, GUILD_B, other) is not None
        assert await announcements.delete(pool, GUILD_A, first)
        assert not await announcements.delete(pool, GUILD_A, first)
        assert [entry.id for entry in await announcements.all_announcements(pool)] == [second, other]

    with_pool(test)


def test_a_member_can_only_be_claimed_once_per_guild():
    async def test(pool):
        await pool.execute("TRUNCATE personal_officer_channels")
        alice, bob = 1, 2
        claims = await asyncio.gather(*(personal_officer_channels.claim(pool, GUILD_A, alice) for _ in range(3)))
        assert sum(claims) == 1
        assert await personal_officer_channels.claim(pool, GUILD_B, alice)
        await personal_officer_channels.register(pool, GUILD_A, bob, 50)
        assert not await personal_officer_channels.claim(pool, GUILD_A, bob)
        await personal_officer_channels.register(pool, GUILD_A, alice, 40)
        assert await personal_officer_channels.list_registered(pool, GUILD_A) == [(alice, 40), (bob, 50)]
        assert await personal_officer_channels.list_registered(pool, GUILD_B) == [(alice, None)]
        assert await personal_officer_channels.unregister(pool, GUILD_A, bob)
        assert not await personal_officer_channels.unregister(pool, GUILD_A, bob)
        assert await personal_officer_channels.claim(pool, GUILD_A, bob)

    with_pool(test)


def test_a_start_snapshot_is_stored_per_guild_and_taken_once():
    async def test(pool):
        await pool.execute("TRUNCATE raid_attendance_starts")
        at = datetime(2026, 10, 14, 0, 0, tzinfo=UTC)
        present = [1, 2**64 - 6]
        await raid_attendance.save_start(pool, GUILD_A, 8, at, [7])
        await raid_attendance.save_start(pool, GUILD_A, 9, at, present)
        await raid_attendance.save_start(pool, GUILD_B, 9, at, [])
        taken = await raid_attendance.take_start(pool, GUILD_A)
        assert taken is not None and (taken.voice_channel_id, taken.taken_at, taken.present) == (9, at, present)
        assert taken.pairs_with(9, at + timedelta(hours=3))
        assert await raid_attendance.take_start(pool, GUILD_A) is None
        b = await raid_attendance.take_start(pool, GUILD_B)
        assert b is not None and b.present == []

    with_pool(test)


def test_tracked_abilities_are_scoped_to_their_guild_and_limited():
    async def test(pool):
        await pool.execute("TRUNCATE wcl_tracked_abilities")
        added = wcl_mechanics.AddOutcome.ADDED
        assert (await wcl_mechanics.add(pool, GUILD_A, 1282408, "Coalesced Venom"))[0] is added
        assert (await wcl_mechanics.add(pool, GUILD_A, 1299838, "Venom Rupture"))[0] is added
        assert (await wcl_mechanics.add(pool, GUILD_A, 1282408, "coalesced venom (soak)"))[0] is wcl_mechanics.AddOutcome.RENAMED
        assert await wcl_mechanics.add(pool, GUILD_A, 5, "VENOM RUPTURE") == (wcl_mechanics.AddOutcome.NAME_TAKEN, 1299838)
        assert (await wcl_mechanics.add(pool, GUILD_B, 1282408, "Venom Rupture"))[0] is added
        assert [ability.name for ability in await wcl_mechanics.list_tracked(pool, GUILD_A)] == ["coalesced venom (soak)", "Venom Rupture"]
        assert not await wcl_mechanics.remove(pool, GUILD_B, "coalesced venom (soak)")
        assert await wcl_mechanics.remove(pool, GUILD_A, "COALESCED VENOM (SOAK)")
        for ability_id in range(100, 100 + wcl_mechanics.MAX_TRACKED - 1):
            assert (await wcl_mechanics.add(pool, GUILD_A, ability_id, f"Ability {ability_id}"))[0] is added
        assert (await wcl_mechanics.add(pool, GUILD_A, 999, "One too many"))[0] is wcl_mechanics.AddOutcome.FULL
        assert (await wcl_mechanics.add(pool, GUILD_A, 100, "Renamed at the limit"))[0] is wcl_mechanics.AddOutcome.RENAMED

    with_pool(test)


def test_ignored_players_are_scoped_to_their_guild_and_limited():
    async def test(pool):
        await pool.execute("TRUNCATE wcl_ignored_players")
        added, already = wcl_ignored_players.AddOutcome.ADDED, wcl_ignored_players.AddOutcome.ALREADY_IGNORED
        assert await wcl_ignored_players.add(pool, GUILD_A, "Tritonian") is added
        assert await wcl_ignored_players.add(pool, GUILD_A, "aâyo") is added
        assert await wcl_ignored_players.add(pool, GUILD_A, "TRITONIAN") is already
        assert await wcl_ignored_players.add(pool, GUILD_A, "AÂYO") is already
        assert await wcl_ignored_players.add(pool, GUILD_B, "Tritonian") is added
        assert await wcl_ignored_players.list_ignored(pool, GUILD_A) == ["aâyo", "Tritonian"]
        assert not await wcl_ignored_players.remove(pool, GUILD_A, "Graveshrug")
        assert await wcl_ignored_players.remove(pool, GUILD_A, "tRiToNiAn")
        assert await wcl_ignored_players.list_ignored(pool, GUILD_B) == ["Tritonian"]
        for n in range(wcl_ignored_players.MAX_IGNORED - 1):
            assert await wcl_ignored_players.add(pool, GUILD_A, f"Player{n}") is added
        assert await wcl_ignored_players.add(pool, GUILD_A, "OneTooMany") is wcl_ignored_players.AddOutcome.FULL

    with_pool(test)


def test_consumable_abilities_are_scoped_to_their_guild_and_limited():
    async def test(pool):
        await pool.execute("TRUNCATE wcl_consumable_abilities")
        added = wcl_consumables.AddOutcome.ADDED
        assert (await wcl_consumables.add(pool, GUILD_A, Kind.HEALTHSTONE, 6262))[0] is added
        assert (await wcl_consumables.add(pool, GUILD_A, Kind.HEALTHSTONE, 452930))[0] is added
        assert (await wcl_consumables.add(pool, GUILD_A, Kind.HEALTH_POTION, 1234768))[0] is added
        assert (await wcl_consumables.add(pool, GUILD_A, Kind.HEALTHSTONE, 6262))[0] is wcl_consumables.AddOutcome.ALREADY_REGISTERED
        assert await wcl_consumables.add(pool, GUILD_A, Kind.HEALTH_POTION, 6262) == (wcl_consumables.AddOutcome.OTHER_KIND, Kind.HEALTHSTONE)
        assert (await wcl_consumables.add(pool, GUILD_B, Kind.HEALTH_POTION, 6262))[0] is added
        registered = await wcl_consumables.list_registered(pool, GUILD_A)
        assert wcl_consumables.ids(registered, Kind.HEALTHSTONE) == [6262, 452930]
        assert wcl_consumables.ids(registered, Kind.HEALTH_POTION) == [1234768]
        assert await wcl_consumables.remove(pool, GUILD_B, 452930) is None
        assert await wcl_consumables.remove(pool, GUILD_A, 452930) is Kind.HEALTHSTONE
        for ability_id in range(100, 100 + wcl_consumables.MAX_PER_KIND - 1):
            assert (await wcl_consumables.add(pool, GUILD_A, Kind.HEALTHSTONE, ability_id))[0] is added
        assert (await wcl_consumables.add(pool, GUILD_A, Kind.HEALTHSTONE, 999))[0] is wcl_consumables.AddOutcome.FULL
        assert (await wcl_consumables.add(pool, GUILD_A, Kind.HEALTH_POTION, 999))[0] is added

    with_pool(test)
