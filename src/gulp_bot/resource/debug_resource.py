"""The moderators' debug commands: make this week's raid notes thread, take an attendance snapshot, preview a
replay."""

from __future__ import annotations

from datetime import datetime

from discord import app_commands
from discord.ext import commands

from ..core.discord_context import GulpContext
from ..core.permission_checks import check_is_moderator, prefix_check
from ..core.schedule_parsing import now_in_new_york
from ..schema.raid_attendance_schema import AttendanceMoment
from .resource_support import meta

NO_RAID_NOTES_CHANNEL = "No raid notes channel is registered for this server; set one with `/settings raid_notes_channel`."


@commands.hybrid_command(
    name="test_raid_notes", description="Immediately create the raid notes forum thread for a given week, without waiting for the schedule."
)
@prefix_check(check_is_moderator)
@app_commands.describe(week="Any date (YYYY-MM-DD) in the target week; defaults to this week")
async def test_raid_notes(ctx: GulpContext, week: str | None = None) -> None:
    # `check_is_moderator` has already established this is a guild.
    guild_id = ctx.guild_id
    if guild_id is None:
        return
    channel_id = ctx.services.guild_settings.get(guild_id).raid_notes_channel_id
    if channel_id is None:
        await ctx.send(NO_RAID_NOTES_CHANNEL)
        return

    if week is None:
        day = now_in_new_york().date()
    else:
        try:
            day = datetime.strptime(week.strip(), "%Y-%m-%d").date()
        except ValueError:
            await ctx.send(f"Couldn't read `{week}` as a date. Use `YYYY-MM-DD`.")
            return

    thread = await ctx.services.raid_notes.week_thread(guild_id, channel_id, day)
    if thread.created:
        await ctx.send(f'Created raid notes thread "{thread.title}": <#{thread.thread_id}>.')
    else:
        await ctx.send(f'That week already has a raid notes thread, "{thread.title}": <#{thread.thread_id}>.')


meta(
    test_raid_notes,
    category="Debug",
    help_text="`week` is any date (`YYYY-MM-DD`) falling in the target week; the thread is titled for that week's Tuesday–Monday span. Defaults to the current week (Eastern) when omitted.",
    ephemeral=True,
)


@commands.hybrid_command(
    name="test_raid_attendance", description="Record who is in the raid voice channel right now and post it to this week's raid notes thread."
)
@prefix_check(check_is_moderator)
async def test_raid_attendance(ctx: GulpContext) -> None:
    # `check_is_moderator` has already established this is a guild.
    guild_id = ctx.guild_id
    if guild_id is None:
        return
    settings = ctx.services.guild_settings.get(guild_id)
    if settings.raid_voice is None:
        await ctx.send("Raid attendance isn't set up for this server; set it with `/settings raid_voice`.")
        return
    if settings.raid_notes_channel_id is None:
        await ctx.send(NO_RAID_NOTES_CHANNEL)
        return

    voice_channel_id = settings.raid_voice.channel_id
    thread_id = await ctx.services.raid_attendance.record_snapshot(
        guild_id, voice_channel_id, settings.raid_notes_channel_id, AttendanceMoment.TEST, now_in_new_york()
    )
    await ctx.send(f"Posted an attendance snapshot of <#{voice_channel_id}> to <#{thread_id}>.")


meta(test_raid_attendance, category="Debug", ephemeral=True)


@commands.hybrid_command(name="test_replay", description="Post a sample delete-and-replay embed here, to preview how a reposted message looks.")
@prefix_check(check_is_moderator)
@app_commands.describe(message="Text to show in the replayed embed")
async def test_replay(ctx: GulpContext, message: str | None = None) -> None:
    content = message if message is not None else "This is a sample deleted message."
    await ctx.services.message_replay.post_sample_replay(ctx.channel.id, ctx.author, content)
    await ctx.send("Posted a sample replay embed.")


meta(
    test_replay,
    category="Debug",
    help_text="Uses you as the stand-in author. Only exercises the embed rendering — it doesn't touch the audit log check or delete anything.",
    ephemeral=True,
)

COMMANDS = [test_raid_notes, test_raid_attendance, test_replay]
