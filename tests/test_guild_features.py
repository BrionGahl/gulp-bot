"""Raid notes, attendance, announcements, delete-and-replay, officer channels, migrations and `/help`."""

import hashlib
import zlib
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from gulp_bot.core.database import from_bigint, rows_affected, to_bigint
from gulp_bot.core.embed import MAX_DESCRIPTION_CHARS, MAX_FIELD_CHARS
from gulp_bot.core.migrations import load_migrations, lock_id, same_checksum
from gulp_bot.mapper.announcement_mapper import render_announcement
from gulp_bot.mapper.help_text_mapper import HELP_FOOTER, command_help_page, help_overview
from gulp_bot.mapper.message_replay_mapper import replay_embed
from gulp_bot.mapper.raid_attendance_embed_mapper import mention_list, snapshot_description, summary_embed
from gulp_bot.repository.replay_message_memory_repository import ReplayMessageMemoryRepository
from gulp_bot.schema.guild_settings_schema import GuildSettings, WclCredentials, WclSource
from gulp_bot.schema.help_schema import HelpEntry, HelpParameter
from gulp_bot.schema.message_replay_schema import DeletionKey, StoredAttachment, StoredMessage
from gulp_bot.schema.raid_attendance_schema import MAX_RAID_HOURS, AttendanceComparison, StartSnapshot
from gulp_bot.service.help_service import find_command, with_paths
from gulp_bot.service.message_replay_service import ModeratorDeletionTracker
from gulp_bot.service.officer_channel_service import channel_name, has_trial_or_raider_role
from gulp_bot.service.raid_attendance_service import compare_attendance
from gulp_bot.service.raid_notes_service import week_start, week_title
from gulp_bot.service.raid_report_service import is_set_up

NEW_YORK = ZoneInfo("America/New_York")


def at():
    return datetime(2026, 10, 13, 20, 0, tzinfo=NEW_YORK)


# --- raid notes ---


def test_weeks_run_tuesday_to_monday():
    day = lambda n: date(2026, 10, n)  # noqa: E731
    assert day(13).weekday() == 1  # a Tuesday
    assert week_start(day(13)) == day(13)
    assert week_start(day(16)) == day(13)
    assert week_start(day(19)) == day(13)
    assert week_start(day(20)) == day(20)
    assert week_start(day(12)) == day(6)
    assert week_title(day(16)) == "Week of Oct 13 - Oct 19"
    assert week_title(date(2026, 12, 30)) == "Week of Dec 29 - Jan 4"


# --- raid attendance ---


def test_snapshot_lists_everyone_present():
    description = snapshot_description(at(), 9, [1, 2])
    assert description.startswith("<#9> · <t:")
    assert description.endswith("**2 present**\n<@1>, <@2>")
    assert snapshot_description(at(), 9, []).endswith("Nobody was in the channel.")


def test_snapshot_cuts_a_long_list_to_fit_one_embed():
    present = [100_000_000_000_000_000 + n for n in range(1, 501)]
    description = snapshot_description(at(), 9, present)
    assert len(description) <= MAX_DESCRIPTION_CHARS
    listed = description.count("<@")
    assert 0 < listed < 500
    assert description.endswith(f"…and {500 - listed} more.")


def test_comparison_reports_who_left_early_and_who_joined_late():
    assert compare_attendance([1, 2, 3], [2, 3, 4, 5]) == AttendanceComparison(2, [1], [4, 5])
    assert compare_attendance([1, 2], [1, 2]) == AttendanceComparison(2, [], [])
    assert compare_attendance([], [1]) == AttendanceComparison(0, [], [1])


def test_a_long_list_is_cut_to_fit_an_embed_field():
    everyone = [100_000_000_000_000_000 + n for n in range(1, 301)]
    text = mention_list(everyone, MAX_FIELD_CHARS)
    assert len(text) <= MAX_FIELD_CHARS
    assert text.endswith(f"…and {300 - text.count('<@')} more.")
    assert mention_list([4, 5], MAX_FIELD_CHARS) == "<@4>, <@5>"


def start_snapshot(present, hours_before_end):
    return StartSnapshot(9, at().astimezone(UTC) - timedelta(hours=hours_before_end), present)


def test_a_start_only_pairs_with_an_end_of_the_same_raid():
    assert start_snapshot([1], 3).pairs_with(9, at())
    assert start_snapshot([1], 0).pairs_with(9, at())
    assert not start_snapshot([1], MAX_RAID_HOURS + 1).pairs_with(9, at())
    assert not start_snapshot([1], -1).pairs_with(9, at())
    assert not start_snapshot([1], 3).pairs_with(10, at())


def test_the_summary_compares_the_end_with_the_start():
    start = start_snapshot([1, 2, 3], 3)
    embed = summary_embed(start, at(), compare_attendance(start.present, [2, 3, 4]))
    assert [field["name"] for field in embed["fields"]] == ["There at both the start and the end", "Left before the end (1)", "Joined after the start (1)"]
    assert [field["value"] for field in embed["fields"]] == ["2", "<@1>", "<@4>"]
    assert "nothing to compare" in summary_embed(None, at(), None)["description"]


# --- announcements ---


def test_render_only_mentions_registered_roles():
    assert render_announcement("@Raider, @Trial hello", GuildSettings(raider_role_id=5)) == "<@&5>, @Trial hello"


# --- Warcraft Logs after the raid ---


def test_the_overview_after_a_raid_needs_credentials_a_source_and_a_raid_notes_channel():
    complete = GuildSettings(wcl_credentials=WclCredentials("id", "secret"), wcl_source=WclSource(1, 2), raid_notes_channel_id=3)
    assert is_set_up(complete)
    for missing in ("wcl_credentials", "wcl_source", "raid_notes_channel_id"):
        settings = complete.copy()
        setattr(settings, missing, None)
        assert not is_set_up(settings)
    assert not is_set_up(GuildSettings())


# --- delete-and-replay ---


def stored(content, attachments=()):
    return StoredMessage(1, "kail", "https://cdn/avatar.png", 7, content, at(), list(attachments))


def test_a_replay_shows_the_text_one_image_and_links_to_the_rest():
    embed = replay_embed(
        stored("look", [StoredAttachment("https://cdn/a.txt", False), StoredAttachment("https://cdn/b.png", True), StoredAttachment("https://cdn/c.png", True)])
    )
    assert embed["author"] == {"name": "kail deleted a message", "icon_url": "https://cdn/avatar.png"}
    assert embed["description"] == "look\n\nhttps://cdn/a.txt\nhttps://cdn/c.png"
    assert embed["image"] == {"url": "https://cdn/b.png"}
    long = replay_embed(stored("x" * 5000))
    assert len(long["description"]) == MAX_DESCRIPTION_CHARS and long["description"].endswith("…")
    assert "description" not in replay_embed(stored("", [StoredAttachment("https://cdn/b.png", True)]))


def test_the_buffer_keeps_the_latest_messages():
    kept = ReplayMessageMemoryRepository()
    for n in range(250):
        kept.remember(n, stored(str(n)))
    assert kept.take(10) is None
    taken = kept.take(249)
    assert taken is not None and taken.content == "249"
    assert kept.take(249) is None


def test_a_new_moderator_deletion_is_spotted_once():
    deletions = ModeratorDeletionTracker()
    key = DeletionKey(guild_id=1, author_id=7, channel_id=1)
    # Nothing to compare with yet: only a recent entry counts.
    assert not deletions.is_new_deletion(key, entry_id=5, count=1, age_secs=60)
    assert deletions.is_new_deletion(key, entry_id=5, count=2, age_secs=600)
    assert not deletions.is_new_deletion(key, entry_id=5, count=2, age_secs=1)
    assert deletions.is_new_deletion(key, entry_id=6, count=1, age_secs=1000)
    assert deletions.is_new_deletion(DeletionKey(1, 8, 1), entry_id=9, count=1, age_secs=3)


# --- personal officer channels ---


def test_a_channel_is_named_after_the_member():
    assert channel_name("The Big T!") == "the-big-t"
    assert channel_name("Rè") == "r"
    assert channel_name("☆☆") == "member"
    assert has_trial_or_raider_role(GuildSettings(raider_role_id=10), [10])
    assert not has_trial_or_raider_role(GuildSettings(raider_role_id=10), [20])
    assert not has_trial_or_raider_role(GuildSettings(), [10])


# --- the database ---


def test_ids_above_the_signed_maximum_round_trip_as_the_rust_bot_stored_them():
    big = 2**64 - 6
    assert to_bigint(big) == -6 and from_bigint(-6) == big
    assert to_bigint(42) == 42 and from_bigint(42) == 42
    assert rows_affected("DELETE 1") == 1 and rows_affected("INSERT 0 1") == 1 and rows_affected("UPDATE 0") == 0


def test_migrations_are_read_as_sqlx_reads_them():
    migrations = load_migrations()
    assert [migration.version for migration in migrations] == list(range(1, 16))
    first = migrations[0]
    assert first.description == "guild settings"
    raw = (Path(__file__).resolve().parent.parent / "migrations" / "0001_guild_settings.sql").read_bytes()
    assert first.checksum == hashlib.sha384(raw).digest()
    # A database migrated from a checkout with the other line endings is the same migration.
    lf = hashlib.sha384(raw.replace(b"\r\n", b"\n")).digest()
    crlf = hashlib.sha384(raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")).digest()
    assert same_checksum(lf, first) and same_checksum(crlf, first)
    assert not same_checksum(hashlib.sha384(b"something else").digest(), first)
    assert lock_id("gulp") == 0x3D32AD9E * zlib.crc32(b"gulp")


# --- /help ---

COMMANDS = [
    HelpEntry("get_timeline_reminders", "Fetches Bart Timeline Reminders addon info if you have a raider role.", category="WoW Guild"),
    HelpEntry("source", "Links to the bot GitHub repo", category="Utilities"),
    HelpEntry("register", "Register slash commands in this guild or globally", category="Utilities", hidden=True),
    HelpEntry(
        "settings",
        "View or change what this server has registered with the bot.",
        category="Settings",
        subcommands=[
            HelpEntry("show", "Show everything registered for this server"),
            HelpEntry("raid_voice", "Record attendance", parameters=[HelpParameter("channel", True, "Voice channel the raid happens in")]),
            HelpEntry("wcl", "Warcraft Logs settings", subcommands=[HelpEntry("client", "API client"), HelpEntry("source", "Report source")]),
        ],
    ),
    HelpEntry("announcement", "Announcements", category="Announcements", subcommands=[HelpEntry("add", "Schedule one")]),
]


def test_the_overview_lists_commands_by_category_without_the_hidden_ones():
    text = help_overview(COMMANDS)
    assert text.startswith("```\nWoW Guild:\n  /get_timeline_reminders Fetches")
    assert "  /source      Links to the bot GitHub repo\n" in text
    assert "/register" not in text
    assert text.endswith(f"\n{HELP_FOOTER}\n```")


def test_find_command_resolves_nested_names_and_ignores_prefixes():
    assert find_command(COMMANDS, "settings")[0] == "settings"  # type: ignore[index]
    assert find_command(COMMANDS, " /Settings  raid_voice ")[0] == "settings raid_voice"  # type: ignore[index]
    assert find_command(COMMANDS, "?announcement add")[0] == "announcement add"  # type: ignore[index]
    assert find_command(COMMANDS, "settings wcl source")[0] == "settings wcl source"  # type: ignore[index]
    for missing in ["settings nope", "settings wcl_source", "nope", "register", ""]:
        assert find_command(COMMANDS, missing) is None

    page = command_help_page("settings", COMMANDS[3])
    assert "**Subcommands**\n`/settings show` — " in page and "`/settings wcl` — " in page
    page = command_help_page("settings raid_voice", COMMANDS[3].subcommands[1])
    assert "**Parameters**\n`channel` (required) — " in page
    assert [path for path, _ in with_paths(COMMANDS)][:4] == ["get_timeline_reminders", "source", "register", "settings show"]
