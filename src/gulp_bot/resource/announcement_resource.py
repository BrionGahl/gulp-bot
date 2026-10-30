"""`/announcement`: schedule, list, post and delete this server's recurring announcements. Moderators.

Slash commands only: the message is collected in a modal (the only way to enter multiple lines), and a modal
can only be opened in response to an interaction.
"""

from __future__ import annotations

import discord
from discord import app_commands

from ..core.discord_context import slash_context
from ..core.discord_helpers import NO_MENTIONS
from ..core.embed import command_embed, to_discord_embed
from ..core.permission_checks import check_is_moderator, slash_check
from ..core.schedule_parsing import describe_schedule, next_fire_discord_timestamp, parse_weekly_schedule
from ..exception.schedule_exception import ScheduleProblem
from ..mapper.announcement_mapper import announcement_list_description
from ..schema.announcement_schema import NewAnnouncement
from ..service.announcement_service import MAX_ANNOUNCEMENTS_PER_GUILD
from ..service.guild_validation_service import TEXT_CHANNEL_TYPES
from .resource_support import meta, reply

# How long the modal waits for the message, in seconds.
MODAL_TIMEOUT = 3600.0


class AnnouncementMessageModal(discord.ui.Modal, title="New announcement"):
    """Asks for the announcement's message, which can run over several lines."""

    message: discord.ui.TextInput[AnnouncementMessageModal] = discord.ui.TextInput(
        label="Message",
        style=discord.TextStyle.paragraph,
        placeholder="@Raider and @Trial are replaced with pings of those roles.",
        min_length=1,
        # Discord's limit on a message's content.
        max_length=2000,
    )

    def __init__(self) -> None:
        super().__init__(timeout=MODAL_TIMEOUT)
        self.submitted: discord.Interaction | None = None

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.submitted = interaction
        self.stop()


# Every subcommand carries the moderator check: a slash subcommand of a group doesn't run the group's checks.
announcement = app_commands.Group(
    name="announcement", description="Schedule, list and delete this server's recurring announcements. Officers only.", guild_only=True
)
meta(announcement, category="Announcements", help_text="Slash commands only. Announcements are posted on days of the week at a time of day, Eastern time.")


@announcement.command(name="add", description="Schedule a recurring announcement; you'll be asked for the message next")
@slash_check(check_is_moderator)
@app_commands.describe(
    channel="Channel to post in",
    days="Day(s) of the week to post on, e.g. Tue, Thu",
    time="Time of day to post at (Eastern), e.g. 12pm or 12:00",
)
async def add(interaction: discord.Interaction, channel: discord.TextChannel, days: str, time: str) -> None:
    ctx = await slash_context(interaction)
    guild_id = ctx.guild_id
    if guild_id is None:
        return
    announcements = ctx.services.announcements

    # Everything that can be rejected is checked before the modal opens, so nobody writes out a message only
    # to be told the schedule was wrong.
    try:
        schedule = parse_weekly_schedule(days, time)
    except ScheduleProblem as problem:
        await reply(ctx, str(problem))
        return
    problem = await ctx.services.guild_validation.channel_problem(guild_id, channel.id, TEXT_CHANNEL_TYPES, "a text channel")
    if problem is not None:
        await reply(ctx, problem)
        return
    if not await announcements.has_room_for_another(guild_id):
        await reply(ctx, f"This server already has {MAX_ANNOUNCEMENTS_PER_GUILD} announcements, the most allowed. Delete one first.")
        return

    modal = AnnouncementMessageModal()
    await interaction.response.send_modal(modal)
    # Dismissed or timed out: there's nothing to reply to.
    if await modal.wait() or modal.submitted is None:
        return

    announcement_id = await announcements.schedule(NewAnnouncement(guild_id, channel.id, schedule, modal.message.value, ctx.author.id))
    await modal.submitted.response.send_message(
        f"Announcement **#{announcement_id}** scheduled for <#{channel.id}>, {describe_schedule(schedule)}. Next post: {next_fire_discord_timestamp(schedule)}.",
        ephemeral=True,
        allowed_mentions=NO_MENTIONS,
    )


@announcement.command(name="list", description="List this server's scheduled announcements")
@slash_check(check_is_moderator)
async def list_announcements(interaction: discord.Interaction) -> None:
    ctx = await slash_context(interaction)
    guild_id = ctx.guild_id
    if guild_id is None:
        return

    scheduled = await ctx.services.announcements.list_for_guild(guild_id)
    if not scheduled:
        await reply(ctx, "No announcements are scheduled for this server. Add one with `/announcement add`.")
        return
    embed = command_embed(
        ctx.services.config.bot_name, title=f"Scheduled Announcements ({len(scheduled)})", description=announcement_list_description(scheduled)
    )
    await ctx.send(embed=to_discord_embed(embed))


@announcement.command(name="delete", description="Delete one of this server's scheduled announcements")
@slash_check(check_is_moderator)
@app_commands.describe(id="Announcement number, from /announcement list")
async def delete(interaction: discord.Interaction, id: int) -> None:
    ctx = await slash_context(interaction)
    guild_id = ctx.guild_id
    if guild_id is None:
        return
    if await ctx.services.announcements.delete(guild_id, id):
        await reply(ctx, f"Announcement **#{id}** deleted.")
    else:
        await reply(ctx, f"This server has no announcement **#{id}**.")


@announcement.command(name="post", description="Post one of this server's announcements right now, without waiting for its schedule")
@slash_check(check_is_moderator)
@app_commands.describe(id="Announcement number, from /announcement list")
async def post(interaction: discord.Interaction, id: int) -> None:
    ctx = await slash_context(interaction)
    guild_id = ctx.guild_id
    if guild_id is None:
        return
    entry = await ctx.services.announcements.find(guild_id, id)
    if entry is None:
        await reply(ctx, f"This server has no announcement **#{id}**.")
        return
    await ctx.services.announcements.post(entry)
    await reply(ctx, f"Posted announcement **#{id}** to <#{entry.channel_id}>.")


for _command in announcement.commands:
    meta(_command, ephemeral=True)

COMMANDS = [announcement]
