"""`/announcement`: schedule, list and delete this server's recurring announcements.

Slash commands only: the message is collected in a modal (the only way to enter multiple lines), and a
modal can only be opened in response to an interaction.
"""

import discord
from discord import app_commands

from .. import announcements, schedules
from ..checks import check_is_moderator, slash_check
from ..context import slash_context
from ..helper import NO_MENTIONS, base_embed
from ..schedules import ScheduleProblem
from .base import meta, reply
from .settings import channel_problem

# Most announcements one server can have scheduled, to keep the list readable and bound how much one
# server can make the bot post.
MAX_ANNOUNCEMENTS_PER_GUILD = 25

# How much of each message `/announcement list` shows.
PREVIEW_CHARS = 80

# Cap on the `/announcement list` embed description, under Discord's 4096-character limit.
MAX_LIST_CHARS = 4000

# How long the modal waits for the message, in seconds.
MODAL_TIMEOUT = 3600.0


class AnnouncementModal(discord.ui.Modal, title="New announcement"):
    message: discord.ui.TextInput["AnnouncementModal"] = discord.ui.TextInput(
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
meta(announcement, category="Announcements", help_text="Slash commands only. Schedules are cron expressions evaluated in Eastern time.")


@announcement.command(name="add", description="Schedule a recurring announcement; you'll be asked for the message next")
@slash_check(check_is_moderator)
@app_commands.describe(
    channel="Channel to post in",
    cron="When to post (Eastern): sec min hour day-of-month month day-of-week [year], e.g. 0 0 12 * * Tue *",
)
async def add(interaction: discord.Interaction, channel: discord.TextChannel, cron: str) -> None:
    ctx = await slash_context(interaction)
    guild_id = ctx.guild_id
    if guild_id is None:
        return
    db = ctx.bot.db

    # Everything that can be rejected is checked before the modal opens, so nobody writes out a message
    # only to be told the schedule was wrong.
    try:
        schedule = schedules.parse_schedule(cron.strip())
    except ScheduleProblem as problem:
        await reply(ctx, str(problem))
        return
    problem = await channel_problem(ctx, guild_id, channel.id, [discord.ChannelType.text, discord.ChannelType.news], "a text channel")
    if problem is not None:
        await reply(ctx, problem)
        return
    if await announcements.count(db, guild_id) >= MAX_ANNOUNCEMENTS_PER_GUILD:
        await reply(ctx, f"This server already has {MAX_ANNOUNCEMENTS_PER_GUILD} announcements, the most allowed. Delete one first.")
        return

    modal = AnnouncementModal()
    await interaction.response.send_modal(modal)
    # Dismissed or timed out: there's nothing to reply to.
    if await modal.wait() or modal.submitted is None:
        return

    announcement_id = await announcements.create(db, guild_id, channel.id, schedule, modal.message.value, ctx.author.id)

    # `parse_schedule` only accepts schedules with an upcoming fire time.
    next_post = schedules.discord_time(schedule)
    await modal.submitted.response.send_message(
        f"Announcement **#{announcement_id}** scheduled for <#{channel.id}> on `{schedule.source}`. Next post: {next_post}.",
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

    scheduled = await announcements.list_announcements(ctx.bot.db, guild_id)
    if not scheduled:
        await reply(ctx, "No announcements are scheduled for this server. Add one with `/announcement add`.")
        return

    total = len(scheduled)
    description = ""
    for shown, entry in enumerate(scheduled):
        next_post = schedules.discord_time(entry.schedule, "R", never="never")
        text = f"**#{entry.id}** — <#{entry.channel_id}> — `{entry.schedule.source}` — next {next_post}\nby <@{entry.created_by}>: {preview(entry.message)}\n\n"
        # Stay inside Discord's embed description limit, leaving room for the "and N more" line.
        if len(description.encode()) + len(text.encode()) > MAX_LIST_CHARS:
            description += f"…and {total - shown} more."
            break
        description += text

    embed = base_embed(ctx.bot.config.bot_name)
    embed.title = f"Scheduled Announcements ({total})"
    embed.description = description
    await ctx.send(embed=embed)


@announcement.command(name="delete", description="Delete one of this server's scheduled announcements")
@slash_check(check_is_moderator)
@app_commands.describe(id="Announcement number, from /announcement list")
async def delete(interaction: discord.Interaction, id: int) -> None:
    ctx = await slash_context(interaction)
    guild_id = ctx.guild_id
    if guild_id is None:
        return
    if await announcements.delete(ctx.bot.db, guild_id, id):
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
    entry = await announcements.get(ctx.bot.db, guild_id, id)
    if entry is None:
        await reply(ctx, f"This server has no announcement **#{id}**.")
        return
    await announcements.post(ctx.bot, entry, ctx.bot.guild_settings.get(guild_id))
    await reply(ctx, f"Posted announcement **#{id}** to <#{entry.channel_id}>.")


for _command in announcement.commands:
    meta(_command, ephemeral=True)


def preview(message: str) -> str:
    """The start of `message` on one line, with formatting neutralised so it reads as a quote of the text
    rather than rendering (or pinging) inside the list."""
    flattened = " ".join(message.split()).replace("`", "'")
    shown = flattened[:PREVIEW_CHARS]
    if len(flattened) > PREVIEW_CHARS:
        shown += "…"
    return f"`{shown}`"
