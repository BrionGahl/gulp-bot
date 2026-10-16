"""`/settings`: what this server has registered with the bot."""

from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from .. import personal_officer_channels, raid_notes, schedules, wcl_consumables, wcl_ignored_players, wcl_mechanics
from ..checks import check_is_admin_or_moderator, check_is_administrator, prefix_check, slash_check
from ..context import GulpContext, slash_context
from ..guild_settings import RaidVoice, Setting, WclCredentials, WclSource
from ..helper import base_embed
from ..schedules import ScheduleProblem
from ..wcl_consumables import ALL_KINDS, Kind
from .base import choice_value, meta, reply, require_guild, subcommand_required
from .wcl import autocomplete_ability, autocomplete_ignored_player

# Cap on the `officer_channel_list` embed description, under Discord's 4096-character limit.
MAX_LIST_CHARS = 4000

# The largest ID Warcraft Logs uses: its IDs are signed 32-bit integers, and zero is never one.
MAX_WCL_ID = 2**31 - 1

U32 = app_commands.Range[int, 0, 2**32 - 1]

# Everything `/settings clear` can clear: each ID setting, plus the credentials. (value, name shown).
CLEAR_TARGETS = [
    ("mod_role", "Moderator role"),
    ("dev_role", "Developer role"),
    ("raider_role", "Raider role"),
    ("trial_role", "Trial role"),
    ("personal_officer_category", "Personal officer category"),
    ("raid_notes_channel", "Raid notes channel"),
    ("raid_notes_cron", "Raid notes schedule"),
    ("raid_voice", "Raid attendance"),
    ("bart_token", "Bart token"),
    ("wowutils", "WowUtils credentials"),
    ("wcl_source", "Warcraft Logs source"),
    ("wcl_client", "Warcraft Logs API client"),
]
CLEAR_CHOICES = [app_commands.Choice(name=name, value=value) for value, name in CLEAR_TARGETS]
CLEAR_NAMES = dict(CLEAR_TARGETS)

KIND_CHOICES = [app_commands.Choice(name=kind.display_name, value=kind.value) for kind in ALL_KINDS]

# The settings that are one ID, by the value `/settings clear` uses for them.
ID_SETTINGS = {
    "mod_role": Setting.MOD_ROLE,
    "dev_role": Setting.DEV_ROLE,
    "raider_role": Setting.RAIDER_ROLE,
    "trial_role": Setting.TRIAL_ROLE,
    "personal_officer_category": Setting.PERSONAL_OFFICER_CATEGORY,
    "raid_notes_channel": Setting.RAID_NOTES_CHANNEL,
}


# The check and `guild_only` are on every subcommand rather than once here: a slash subcommand of a group
# doesn't run the group's checks. There is deliberately no `default_permissions`: Discord would hide the
# command from moderators who aren't administrators.
@commands.hybrid_group(name="settings", description="View or change what this server has registered with the bot. Administrators and moderators.")
@commands.guild_only()
@app_commands.guild_only()
async def settings(ctx: GulpContext) -> None:
    await subcommand_required(ctx)


meta(
    settings,
    category="Settings",
    help_text="Everything here applies only to the server the command is used in. Setting the moderator role is limited to administrators.",
    ephemeral=True,
)


def _sub(command: Any, help_text: str | None = None) -> None:
    meta(command, help_text=help_text, ephemeral=True)


@settings.command(name="show", description="Show everything registered for this server")
@prefix_check(check_is_admin_or_moderator)
async def show(ctx: GulpContext) -> None:
    guild_id = require_guild(ctx)
    current = ctx.bot.guild_settings.get(guild_id)

    def role(role_id: int | None) -> str:
        return "Not set" if role_id is None else f"<@&{role_id}>"

    def channel(channel_id: int | None) -> str:
        return "Not set" if channel_id is None else f"<#{channel_id}>"

    clips = ", ".join(f"<#{channel_id}>" for channel_id in sorted(current.clips_channel_ids)) or "None"
    replay_users = ", ".join(f"<@{user_id}>" for user_id in sorted(current.replay_user_ids)) or "None"

    raid_voice = "Not set"
    if current.raid_voice is not None:
        raid_voice = f"<#{current.raid_voice.channel_id}> — start `{current.raid_voice.start.source}`, end `{current.raid_voice.end.source}`"

    if current.raid_notes_cron is not None:
        raid_notes_schedule = f"`{current.raid_notes_cron.source}`"
    else:
        raid_notes_schedule = f"Default (`{raid_notes.DEFAULT_RAID_NOTES_CRON}`)"

    wcl_source = "Not set" if current.wcl_source is None else f"Guild {current.wcl_source.guild_id}, uploader {current.wcl_source.uploader_id}"

    # Names are limited when they're added (`wcl_mechanics.MAX_NAME_CHARS`, `MAX_TRACKED`), so the list
    # always fits in one field.
    abilities = await wcl_mechanics.list_tracked(ctx.bot.db, guild_id)
    wcl_abilities = ", ".join(ability.name for ability in abilities) or "None"

    # Only how many: the names can add up to more than a field holds, and `/settings wcl ignore_list` has them.
    ignored_count = len(await wcl_ignored_players.list_ignored(ctx.bot.db, guild_id))
    wcl_ignored = {0: "None", 1: "1 player"}.get(ignored_count, f"{ignored_count} players")

    # At most `MAX_PER_KIND` IDs of each kind, so this is always short.
    registered = await wcl_consumables.list_registered(ctx.bot.db, guild_id)
    consumable_lines = []
    for kind in ALL_KINDS:
        ids = wcl_consumables.ids(registered, kind)
        if ids:
            consumable_lines.append(f"{kind.plural}: {', '.join(map(str, ids))}")
    wcl_consumables_text = "\n".join(consumable_lines) or "None"

    embed = base_embed(ctx.bot.config.bot_name)
    embed.title = "Server Settings"
    embed.add_field(name=Setting.MOD_ROLE.value, value=role(current.mod_role_id), inline=True)
    embed.add_field(name=Setting.DEV_ROLE.value, value=role(current.dev_role_id), inline=True)
    embed.add_field(name=Setting.RAIDER_ROLE.value, value=role(current.raider_role_id), inline=True)
    embed.add_field(name=Setting.TRIAL_ROLE.value, value=role(current.trial_role_id), inline=True)
    embed.add_field(name=Setting.PERSONAL_OFFICER_CATEGORY.value, value=channel(current.personal_officer_category_id), inline=True)
    embed.add_field(name=Setting.RAID_NOTES_CHANNEL.value, value=channel(current.raid_notes_channel_id), inline=True)
    embed.add_field(name=CLEAR_NAMES["raid_notes_cron"], value=raid_notes_schedule, inline=True)
    embed.add_field(name=CLEAR_NAMES["raid_voice"], value=raid_voice, inline=False)
    embed.add_field(name="Clips channels", value=clips, inline=False)
    embed.add_field(name="Replay users", value=replay_users, inline=False)
    # Credentials are never echoed back, only whether they're registered.
    embed.add_field(name=CLEAR_NAMES["bart_token"], value="Set" if current.bart_token is not None else "Not set", inline=True)
    embed.add_field(name=CLEAR_NAMES["wowutils"], value="Set" if current.wowutils_token is not None else "Not set", inline=True)
    embed.add_field(name=CLEAR_NAMES["wcl_source"], value=wcl_source, inline=True)
    embed.add_field(name=CLEAR_NAMES["wcl_client"], value="Set" if current.wcl_credentials is not None else "Not set", inline=True)
    embed.add_field(name="Warcraft Logs abilities", value=wcl_abilities, inline=False)
    embed.add_field(name="Warcraft Logs ignored players", value=wcl_ignored, inline=True)
    embed.add_field(name="Warcraft Logs consumables", value=wcl_consumables_text, inline=True)
    await ctx.send(embed=embed)


_sub(show)


@settings.command(name="mod_role", description="Register the moderator (officer) role for this server. Administrators only.")
@prefix_check(check_is_admin_or_moderator)
@prefix_check(check_is_administrator)
@app_commands.describe(role="Role that grants moderator commands")
async def mod_role(ctx: GulpContext, role: discord.Role) -> None:
    await register_role(ctx, Setting.MOD_ROLE, role.id)


_sub(mod_role)


@settings.command(name="dev_role", description="Register the developer role for this server. Administrators only.")
@prefix_check(check_is_admin_or_moderator)
@prefix_check(check_is_administrator)
@app_commands.describe(role="Role held by the bot's developers")
async def dev_role(ctx: GulpContext, role: discord.Role) -> None:
    guild_id = require_guild(ctx)
    # The role would otherwise hand developer powers to every moderator, raider or trial.
    current = ctx.bot.guild_settings.get(guild_id)
    if role.id in (current.mod_role_id, current.raider_role_id, current.trial_role_id):
        await reply(ctx, "That role is registered as the moderator, raider or trial role. Pick a role that only developers hold.")
        return
    await register_role(ctx, Setting.DEV_ROLE, role.id)


_sub(
    dev_role,
    "Developers run the `/ai` commands and rank above moderators for the AI assistant. Pick a role that only developers hold: it can't be the moderator, raider or trial role.",
)


@settings.command(name="raider_role", description="Register the raider role for this server")
@prefix_check(check_is_admin_or_moderator)
@app_commands.describe(role="Role held by raiders")
async def raider_role(ctx: GulpContext, role: discord.Role) -> None:
    await register_role(ctx, Setting.RAIDER_ROLE, role.id)


_sub(raider_role)


@settings.command(name="trial_role", description="Register the trial role for this server")
@prefix_check(check_is_admin_or_moderator)
@app_commands.describe(role="Role held by trial raiders")
async def trial_role(ctx: GulpContext, role: discord.Role) -> None:
    await register_role(ctx, Setting.TRIAL_ROLE, role.id)


_sub(trial_role)


@settings.command(name="officer_category", description="Register the category that personal officer channels are created under")
@prefix_check(check_is_admin_or_moderator)
@app_commands.describe(category="Category for personal officer channels")
async def officer_category(ctx: GulpContext, category: discord.CategoryChannel) -> None:
    await register_channel(ctx, Setting.PERSONAL_OFFICER_CATEGORY, category.id, [discord.ChannelType.category], "a category")


_sub(officer_category)


@settings.command(name="officer_channel_add", description="Register a personal officer channel that already exists for a member")
@prefix_check(check_is_admin_or_moderator)
@app_commands.describe(user="Member the channel belongs to", channel="Their existing personal officer channel")
async def officer_channel_add(ctx: GulpContext, user: discord.User, channel: discord.TextChannel) -> None:
    guild_id = require_guild(ctx)
    if not await is_member(ctx, guild_id, user.id):
        await reply(ctx, f"<@{user.id}> isn't a member of this server, so they can't be registered here.")
        return
    problem = await channel_problem(ctx, guild_id, channel.id, [discord.ChannelType.text], "a text channel")
    if problem is not None:
        await reply(ctx, problem)
        return

    await personal_officer_channels.register(ctx.bot.db, guild_id, user.id, channel.id)
    await reply(ctx, f"<#{channel.id}> registered as <@{user.id}>'s personal officer channel. The bot won't create one for them.")


_sub(
    officer_channel_add,
    "Once a member is registered the bot never creates a channel for them, so this is how channels made by hand (or before the bot tracked them) are made known to it.",
)


@settings.command(name="officer_channel_remove", description="Forget a member's personal officer channel, so the bot will create one for them again")
@prefix_check(check_is_admin_or_moderator)
@app_commands.describe(user="Member to forget the channel for")
async def officer_channel_remove(ctx: GulpContext, user: discord.User) -> None:
    guild_id = require_guild(ctx)
    # No membership check: the member may have left the server since they were registered.
    if await personal_officer_channels.unregister(ctx.bot.db, guild_id, user.id):
        await reply(ctx, f"<@{user.id}> no longer has a registered personal officer channel. The channel itself was not deleted.")
    else:
        await reply(ctx, f"<@{user.id}> has no registered personal officer channel in this server.")


_sub(officer_channel_remove)


@settings.command(name="officer_channel_list", description="List the members who have a registered personal officer channel")
@prefix_check(check_is_admin_or_moderator)
async def officer_channel_list(ctx: GulpContext) -> None:
    guild_id = require_guild(ctx)
    registered = await personal_officer_channels.list_registered(ctx.bot.db, guild_id)
    if not registered:
        await reply(ctx, "No personal officer channels are registered for this server.")
        return

    total = len(registered)
    description = ""
    for shown, (user_id, channel_id) in enumerate(registered):
        line = f"<@{user_id}> — <#{channel_id}>\n" if channel_id is not None else f"<@{user_id}> — channel not recorded\n"
        # Stay inside Discord's embed description limit, leaving room for the "and N more" line.
        if len(description.encode()) + len(line.encode()) > MAX_LIST_CHARS:
            description += f"…and {total - shown} more."
            break
        description += line

    embed = base_embed(ctx.bot.config.bot_name)
    embed.title = f"Personal Officer Channels ({total})"
    embed.description = description
    await ctx.send(embed=embed)


_sub(officer_channel_list)


@settings.command(name="raid_notes_channel", description="Register the forum channel that weekly raid notes threads are posted in")
@prefix_check(check_is_admin_or_moderator)
@app_commands.describe(channel="Forum channel for weekly raid notes")
async def raid_notes_channel(ctx: GulpContext, channel: discord.ForumChannel) -> None:
    await register_channel(ctx, Setting.RAID_NOTES_CHANNEL, channel.id, [discord.ChannelType.forum], "a forum channel")


_sub(raid_notes_channel)


@settings.command(name="raid_notes_cron", description="Set when the raid notes post is created, as a quartz-style cron expression (Eastern time)")
@prefix_check(check_is_admin_or_moderator)
@app_commands.describe(expression="sec min hour day-of-month month day-of-week [year], e.g. 0 0 9 * * Fri *")
async def raid_notes_cron(ctx: GulpContext, *, expression: str) -> None:
    guild_id = require_guild(ctx)
    try:
        schedule = schedules.parse_schedule(expression.strip())
    except ScheduleProblem as problem:
        await reply(ctx, str(problem))
        return
    # `parse_schedule` only accepts schedules with an upcoming fire time.
    next_post = schedules.discord_time(schedule)

    await ctx.bot.guild_settings.set_raid_notes_cron(guild_id, schedule)
    await reply(ctx, f"{CLEAR_NAMES['raid_notes_cron']} set to `{schedule.source}`. Next post: {next_post}.")


_sub(raid_notes_cron)


@settings.command(name="raid_voice", description="Record who is in the raid voice channel at the start and end of each raid (Eastern time)")
@prefix_check(check_is_admin_or_moderator)
@app_commands.describe(
    channel="Voice channel the raid happens in",
    start="Raid start: sec min hour day-of-month month day-of-week [year], e.g. 0 0 20 * * Tue,Thu *",
    end="Raid end, same format, e.g. 0 0 23 * * Tue,Thu *",
)
async def raid_voice(ctx: GulpContext, channel: discord.VoiceChannel | discord.StageChannel, start: str, end: str) -> None:
    guild_id = require_guild(ctx)
    problem = await channel_problem(ctx, guild_id, channel.id, [discord.ChannelType.voice, discord.ChannelType.stage_voice], "a voice channel")
    if problem is not None:
        await reply(ctx, problem)
        return
    try:
        start_schedule = schedules.parse_schedule(start.strip())
    except ScheduleProblem as e:
        await reply(ctx, f"Start: {e}")
        return
    try:
        end_schedule = schedules.parse_schedule(end.strip())
    except ScheduleProblem as e:
        await reply(ctx, f"End: {e}")
        return

    # `parse_schedule` only accepts schedules with an upcoming fire time.
    message = (
        f"{CLEAR_NAMES['raid_voice']} set to <#{channel.id}>. "
        f"Next start: {schedules.discord_time(start_schedule)}. Next end: {schedules.discord_time(end_schedule)}."
    )
    if ctx.bot.guild_settings.get(guild_id).raid_notes_channel_id is None:
        message += (
            "\nAttendance is posted in the week's raid notes thread, and no raid notes channel is registered yet; set one with `/settings raid_notes_channel`."
        )

    await ctx.bot.guild_settings.set_raid_voice(guild_id, RaidVoice(channel.id, start_schedule, end_schedule))
    await reply(ctx, message)


_sub(raid_voice, "Both schedules are quartz-style cron expressions. Through the prefix form, each one has to be wrapped in quotes, since they contain spaces.")


@settings.command(name="replay_add", description="Add a member whose self-deleted messages the bot reposts")
@prefix_check(check_is_admin_or_moderator)
@app_commands.describe(user="Member to replay deleted messages for")
async def replay_add(ctx: GulpContext, user: discord.User) -> None:
    guild_id = require_guild(ctx)
    if not await is_member(ctx, guild_id, user.id):
        await reply(ctx, f"<@{user.id}> isn't a member of this server, so they can't be registered here.")
        return
    await ctx.bot.guild_settings.add_replay_user(guild_id, user.id)
    await reply(ctx, f"<@{user.id}> is now a replay user.")


_sub(replay_add)


@settings.command(name="replay_remove", description="Stop reposting a member's self-deleted messages")
@prefix_check(check_is_admin_or_moderator)
@app_commands.describe(user="Replay user to remove")
async def replay_remove(ctx: GulpContext, user: discord.User) -> None:
    guild_id = require_guild(ctx)
    # No membership check: the member may have left the server since they were added.
    if await ctx.bot.guild_settings.remove_replay_user(guild_id, user.id):
        await reply(ctx, f"<@{user.id}> is no longer a replay user.")
    else:
        await reply(ctx, f"<@{user.id}> isn't a replay user in this server.")


_sub(replay_remove)


@settings.command(name="clips_add", description="Restrict a channel to messages that contain a video")
@prefix_check(check_is_admin_or_moderator)
@app_commands.describe(channel="Channel to restrict to video-only messages")
async def clips_add(ctx: GulpContext, channel: discord.TextChannel) -> None:
    guild_id = require_guild(ctx)
    problem = await channel_problem(ctx, guild_id, channel.id, [discord.ChannelType.text, discord.ChannelType.news], "a text channel")
    if problem is not None:
        await reply(ctx, problem)
        return
    await ctx.bot.guild_settings.add_clips_channel(guild_id, channel.id)
    await reply(ctx, f"<#{channel.id}> is now a clips channel.")


_sub(clips_add)


@settings.command(name="clips_remove", description="Stop restricting a channel to video-only messages")
@prefix_check(check_is_admin_or_moderator)
@app_commands.describe(channel="Clips channel to stop restricting")
async def clips_remove(ctx: GulpContext, channel: discord.abc.GuildChannel) -> None:
    guild_id = require_guild(ctx)
    # No ownership check: this only ever removes a row registered under this guild, and the channel may
    # have been deleted since it was added.
    if await ctx.bot.guild_settings.remove_clips_channel(guild_id, channel.id):
        await reply(ctx, f"<#{channel.id}> is no longer a clips channel.")
    else:
        await reply(ctx, f"<#{channel.id}> isn't registered as a clips channel in this server.")


_sub(clips_remove)


# Slash command only, so the token is never typed into a channel as a normal message.
@app_commands.command(name="bart_token", description="Register the Bart Timeline Reminders token shown to this server's raiders")
@slash_check(check_is_admin_or_moderator)
@app_commands.describe(token="Bart Timeline Reminders access token")
async def bart_token(interaction: discord.Interaction, token: str) -> None:
    ctx = await slash_context(interaction)
    guild_id = require_guild(ctx)
    token = token.strip()
    if not token:
        await reply(ctx, "The token can't be empty.")
        return
    await ctx.bot.guild_settings.set_bart_token(guild_id, token)
    await reply(ctx, f"{CLEAR_NAMES['bart_token']} registered for this server.")


meta(bart_token, help_text="Slash command only, so the token is never typed into a channel as a normal message.", ephemeral=True)
settings.app_command.add_command(bart_token)


@app_commands.command(name="wowutils", description="Register the WowUtils token and group that this server's droptimizers are submitted to")
@slash_check(check_is_admin_or_moderator)
@app_commands.describe(token="WowUtils API token", group_id="WowUtils group ID to submit droptimizer reports to")
async def wowutils(interaction: discord.Interaction, token: str, group_id: str) -> None:
    ctx = await slash_context(interaction)
    guild_id = require_guild(ctx)
    token, group_id = token.strip(), group_id.strip()
    if not token or not group_id:
        await reply(ctx, "The token and group ID can't be empty.")
        return
    await ctx.bot.guild_settings.set_wowutils(guild_id, (token, group_id))
    await reply(ctx, f"{CLEAR_NAMES['wowutils']} registered for this server.")


meta(wowutils, ephemeral=True)
settings.app_command.add_command(wowutils)


# A group of its own because Discord allows a command at most 25 subcommands, and `/settings` had more
# than that with these directly under it.
@settings.group(name="wcl", description="Warcraft Logs settings: API client, report source, abilities, ignored players, consumables")
async def settings_wcl(ctx: GulpContext) -> None:
    await subcommand_required(ctx)


meta(settings_wcl, ephemeral=True)


@app_commands.command(name="client", description="Register the Warcraft Logs API client this server's `/wcl` commands use")
@slash_check(check_is_admin_or_moderator)
@app_commands.describe(client_id="Warcraft Logs API client ID", client_secret="Warcraft Logs API client secret")
async def wcl_client(interaction: discord.Interaction, client_id: str, client_secret: str) -> None:
    ctx = await slash_context(interaction)
    guild_id = require_guild(ctx)
    client_id, client_secret = client_id.strip(), client_secret.strip()
    if not client_id or not client_secret:
        await reply(ctx, "The client ID and secret can't be empty.")
        return
    await ctx.bot.guild_settings.set_wcl_credentials(guild_id, WclCredentials(client_id, client_secret))
    await reply(ctx, f"{CLEAR_NAMES['wcl_client']} registered for this server.")


meta(wcl_client, help_text="Create one at warcraftlogs.com (Profile, then Manage Your Clients) and give its ID and secret.", ephemeral=True)
settings_wcl.app_command.add_command(wcl_client)


@settings_wcl.command(name="source", description="Register where this server's Warcraft Logs reports are found: the guild and the uploader")
@prefix_check(check_is_admin_or_moderator)
@app_commands.describe(guild="Warcraft Logs guild ID the reports are uploaded to", uploader="Warcraft Logs user ID of the account that uploads the logs")
async def wcl_source(ctx: GulpContext, guild: U32, uploader: U32) -> None:
    guild_id = require_guild(ctx)
    # The API's IDs are signed 32-bit integers, and zero is never one.
    if not (1 <= guild <= MAX_WCL_ID and 1 <= uploader <= MAX_WCL_ID):
        await reply(ctx, "Those aren't valid Warcraft Logs IDs. Both are positive whole numbers.")
        return
    await ctx.bot.guild_settings.set_wcl_source(guild_id, WclSource(guild, uploader))
    await reply(ctx, f"{CLEAR_NAMES['wcl_source']} set to guild {guild}, uploader {uploader}.")


_sub(wcl_source, "Both are the numeric IDs Warcraft Logs uses. Only reports from this uploader are picked when `/wcl report` is run without a link.")


@settings_wcl.command(name="mechanic_add", description="Track an ability in the Warcraft Logs overview, to see who took damage from it")
@prefix_check(check_is_admin_or_moderator)
@app_commands.describe(name="Name to show, e.g. Coalesced Venom", ability_id="The game's ability (spell) ID")
async def wcl_mechanic_add(ctx: GulpContext, name: str, ability_id: U32) -> None:
    guild_id = require_guild(ctx)
    name = name.strip()
    if not name or len(name) > wcl_mechanics.MAX_NAME_CHARS:
        await reply(ctx, f"The name has to be 1 to {wcl_mechanics.MAX_NAME_CHARS} characters.")
        return
    # The API's IDs are signed 32-bit integers, and zero is never one.
    if not 1 <= ability_id <= MAX_WCL_ID:
        await reply(ctx, "That isn't a valid ability ID. It's a positive whole number.")
        return

    outcome, other = await wcl_mechanics.add(ctx.bot.db, guild_id, ability_id, name)
    if outcome is wcl_mechanics.AddOutcome.ADDED:
        message = f"Tracking **{name}** (ability {ability_id})."
    elif outcome is wcl_mechanics.AddOutcome.RENAMED:
        message = f"Ability {ability_id} is now called **{name}**."
    elif outcome is wcl_mechanics.AddOutcome.NAME_TAKEN:
        message = f"Another ability (ID {other}) is already called **{name}**. Pick a different name."
    else:
        message = f"This server already tracks {wcl_mechanics.MAX_TRACKED} abilities. Remove one with `/settings wcl mechanic_remove` first."
    await reply(ctx, message)


_sub(
    wcl_mechanic_add,
    "Give a name to show and the ability's ID as Warcraft Logs logs its damage: click the ability in a report's Damage Taken tab. "
    "It can differ from the ID on Wowhead, and one ability can be logged under more than one ID. Adding an ability that is already tracked renames it.",
)


@settings_wcl.command(name="mechanic_remove", description="Stop tracking an ability in the Warcraft Logs overview")
@prefix_check(check_is_admin_or_moderator)
@app_commands.describe(name="Tracked ability to remove")
async def wcl_mechanic_remove(ctx: GulpContext, name: str) -> None:
    guild_id = require_guild(ctx)
    if await wcl_mechanics.remove(ctx.bot.db, guild_id, name.strip()):
        await reply(ctx, f"No longer tracking **{name.strip()}**.")
    else:
        await reply(ctx, f"No tracked ability is called **{name.strip()}**. See `/settings wcl mechanic_list`.")


_sub(wcl_mechanic_remove)
wcl_mechanic_remove.autocomplete("name")(autocomplete_ability)


@settings_wcl.command(name="mechanic_list", description="List the abilities tracked in the Warcraft Logs overview")
@prefix_check(check_is_admin_or_moderator)
async def wcl_mechanic_list(ctx: GulpContext) -> None:
    guild_id = require_guild(ctx)
    tracked = await wcl_mechanics.list_tracked(ctx.bot.db, guild_id)
    if not tracked:
        await reply(ctx, "No abilities are tracked. Add one with `/settings wcl mechanic_add`.")
        return
    lines = "\n".join(f"**{ability.name}** — ability {ability.ability_id}" for ability in tracked)
    await reply(ctx, f"Tracked abilities ({len(tracked)} of {wcl_mechanics.MAX_TRACKED}):\n{lines}")


_sub(wcl_mechanic_list)


@settings_wcl.command(name="ignore_add", description="Leave a player out of the Warcraft Logs overview")
@prefix_check(check_is_admin_or_moderator)
@app_commands.describe(name="Character name, e.g. Tritonian")
async def wcl_ignore_add(ctx: GulpContext, name: str) -> None:
    guild_id = require_guild(ctx)
    name = name.strip()
    if not name or len(name) > wcl_ignored_players.MAX_NAME_CHARS:
        await reply(ctx, f"The name has to be 1 to {wcl_ignored_players.MAX_NAME_CHARS} characters.")
        return

    outcome = await wcl_ignored_players.add(ctx.bot.db, guild_id, name)
    if outcome is wcl_ignored_players.AddOutcome.ADDED:
        message = f"**{name}** is now left out of the Warcraft Logs overview."
    elif outcome is wcl_ignored_players.AddOutcome.ALREADY_IGNORED:
        message = f"**{name}** is already ignored."
    else:
        message = f"This server already ignores {wcl_ignored_players.MAX_IGNORED} players. Remove one with `/settings wcl ignore_remove` first."
    await reply(ctx, message)


_sub(
    wcl_ignore_add,
    "An ignored player appears in no ranking, and what they took doesn't count towards any total. Give the character name; capitals don't matter. A server can ignore 50 players.",
)


@settings_wcl.command(name="ignore_remove", description="Show a player in the Warcraft Logs overview again")
@prefix_check(check_is_admin_or_moderator)
@app_commands.describe(name="Ignored character to show again")
async def wcl_ignore_remove(ctx: GulpContext, name: str) -> None:
    guild_id = require_guild(ctx)
    if await wcl_ignored_players.remove(ctx.bot.db, guild_id, name.strip()):
        await reply(ctx, f"**{name.strip()}** is no longer ignored.")
    else:
        await reply(ctx, f"**{name.strip()}** isn't ignored. See `/settings wcl ignore_list`.")


_sub(wcl_ignore_remove)
wcl_ignore_remove.autocomplete("name")(autocomplete_ignored_player)


@settings_wcl.command(name="ignore_list", description="List the players left out of the Warcraft Logs overview")
@prefix_check(check_is_admin_or_moderator)
async def wcl_ignore_list(ctx: GulpContext) -> None:
    guild_id = require_guild(ctx)
    ignored = await wcl_ignored_players.list_ignored(ctx.bot.db, guild_id)
    if not ignored:
        await reply(ctx, "No players are ignored. Add one with `/settings wcl ignore_add`.")
        return
    await reply(ctx, f"Ignored players ({len(ignored)} of {wcl_ignored_players.MAX_IGNORED}): {', '.join(ignored)}")


_sub(wcl_ignore_list)


@settings_wcl.command(name="consumable_add", description="Count an ability as a healthstone or a health potion in the Warcraft Logs consumables overview")
@prefix_check(check_is_admin_or_moderator)
@app_commands.describe(kind="Healthstone or health potion", ability_id="The ability's ID as Warcraft Logs logs it")
@app_commands.choices(kind=KIND_CHOICES)
async def wcl_consumable_add(ctx: GulpContext, kind: str, ability_id: U32) -> None:
    guild_id = require_guild(ctx)
    value = choice_value(kind, KIND_CHOICES)
    if value is None:
        await reply(ctx, f"`{kind}` isn't one of: {', '.join(choice.name for choice in KIND_CHOICES)}.")
        return
    chosen = Kind(value)
    # The API's IDs are signed 32-bit integers, and zero is never one.
    if not 1 <= ability_id <= MAX_WCL_ID:
        await reply(ctx, "That isn't a valid ability ID. It's a positive whole number.")
        return

    outcome, other = await wcl_consumables.add(ctx.bot.db, guild_id, chosen, ability_id)
    kind_name = chosen.display_name.lower()
    if outcome is wcl_consumables.AddOutcome.ADDED:
        message = f"Ability {ability_id} now counts as a **{kind_name}**."
    elif outcome is wcl_consumables.AddOutcome.ALREADY_REGISTERED:
        message = f"Ability {ability_id} already counts as a **{kind_name}**."
    elif outcome is wcl_consumables.AddOutcome.OTHER_KIND and other is not None:
        message = f"Ability {ability_id} is registered as a **{other.display_name.lower()}**. Remove it with `/settings wcl consumable_remove` first."
    else:
        message = (
            f"This server already has {wcl_consumables.MAX_PER_KIND} abilities registered as a {kind_name}. "
            "Remove one with `/settings wcl consumable_remove` first."
        )
    await reply(ctx, message)


_sub(
    wcl_consumable_add,
    "Give the ability's ID as Warcraft Logs logs it: click it in a report's Casts tab. A kind can have several (a plain and a demonic healthstone, two potions), "
    "and only the IDs you add are counted.",
)


@settings_wcl.command(name="consumable_remove", description="Stop counting an ability in the Warcraft Logs consumables overview")
@prefix_check(check_is_admin_or_moderator)
@app_commands.describe(ability_id="The ability's ID, as in wcl consumable_list")
async def wcl_consumable_remove(ctx: GulpContext, ability_id: U32) -> None:
    guild_id = require_guild(ctx)
    kind = await wcl_consumables.remove(ctx.bot.db, guild_id, ability_id)
    if kind is not None:
        await reply(ctx, f"Ability {ability_id} no longer counts as a **{kind.display_name.lower()}**.")
    else:
        await reply(ctx, f"Ability {ability_id} isn't registered. See `/settings wcl consumable_list`.")


_sub(wcl_consumable_remove)


@settings_wcl.command(name="consumable_list", description="List the abilities counted as healthstones and health potions")
@prefix_check(check_is_admin_or_moderator)
async def wcl_consumable_list(ctx: GulpContext) -> None:
    guild_id = require_guild(ctx)
    registered = await wcl_consumables.list_registered(ctx.bot.db, guild_id)
    if not registered:
        await reply(ctx, "No abilities are registered. Add one with `/settings wcl consumable_add`.")
        return
    lines = []
    for kind in ALL_KINDS:
        ids = wcl_consumables.ids(registered, kind)
        lines.append(f"**{kind.plural}**: {', '.join(map(str, ids)) if ids else 'none'}")
    await reply(ctx, "\n".join(lines))


_sub(wcl_consumable_list)


@settings.command(name="clear", description="Clear one of this server's registered settings")
@prefix_check(check_is_admin_or_moderator)
@app_commands.describe(setting="Setting to clear")
@app_commands.choices(setting=CLEAR_CHOICES)
async def clear(ctx: GulpContext, setting: str) -> None:
    guild_id = require_guild(ctx)
    target = choice_value(setting, CLEAR_CHOICES)
    if target is None:
        await reply(ctx, f"`{setting}` isn't one of: {', '.join(choice.name for choice in CLEAR_CHOICES)}.")
        return
    store = ctx.bot.guild_settings

    # Same restriction as `mod_role` and `dev_role`: clearing one of these roles is changing it.
    if target in ("mod_role", "dev_role") and not await check_is_administrator(ctx):
        return

    if target in ID_SETTINGS:
        await store.set(guild_id, ID_SETTINGS[target], None)
    elif target == "raid_notes_cron":
        await store.set_raid_notes_cron(guild_id, None)
    elif target == "raid_voice":
        await store.set_raid_voice(guild_id, None)
    elif target == "bart_token":
        await store.set_bart_token(guild_id, None)
    elif target == "wowutils":
        await store.set_wowutils(guild_id, None)
    elif target == "wcl_source":
        await store.set_wcl_source(guild_id, None)
    elif target == "wcl_client":
        await store.set_wcl_credentials(guild_id, None)
    await reply(ctx, f"{CLEAR_NAMES[target]} cleared.")


_sub(clear)


async def _guild(ctx: GulpContext, guild_id: int) -> discord.Guild:
    return ctx.bot.get_guild(guild_id) or await ctx.bot.fetch_guild(guild_id)


async def register_role(ctx: GulpContext, setting: Setting, role_id: int) -> None:
    """Registers `role_id` for `setting` after confirming, against Discord, that the role belongs to the
    server the command was used in."""
    guild_id = require_guild(ctx)
    if role_id == guild_id:
        await reply(ctx, "`@everyone` can't be registered; pick a specific role.")
        return
    roles = await (await _guild(ctx, guild_id)).fetch_roles()
    if not any(role.id == role_id for role in roles):
        await reply(ctx, "That role doesn't belong to this server, so it can't be registered here.")
        return

    await ctx.bot.guild_settings.set(guild_id, setting, role_id)
    await reply(ctx, f"{setting.value} set to <@&{role_id}>.")


async def register_channel(ctx: GulpContext, setting: Setting, channel_id: int, allowed: list[discord.ChannelType], expected: str) -> None:
    """Registers `channel_id` for `setting` after confirming, against Discord, that the channel belongs to
    the server the command was used in and is one of the `allowed` types."""
    guild_id = require_guild(ctx)
    problem = await channel_problem(ctx, guild_id, channel_id, allowed, expected)
    if problem is not None:
        await reply(ctx, problem)
        return

    await ctx.bot.guild_settings.set(guild_id, setting, channel_id)
    await reply(ctx, f"{setting.value} set to <#{channel_id}>.")


async def channel_problem(ctx: GulpContext, guild_id: int, channel_id: int, allowed: list[discord.ChannelType], expected: str) -> str | None:
    """Why `channel_id` can't be registered in `guild_id`, or `None` if it can. The channel list is fetched
    from Discord rather than trusted from the command arguments, since a prefix command can be handed
    any ID."""
    channels = await (await _guild(ctx, guild_id)).fetch_channels()
    channel = next((channel for channel in channels if channel.id == channel_id), None)
    if channel is None:
        return "That channel doesn't belong to this server, so it can't be registered here."
    if channel.type not in allowed:
        return f"<#{channel_id}> must be {expected}."
    return None


async def is_member(ctx: GulpContext, guild_id: int, user_id: int) -> bool:
    """Whether `user_id` is a member of `guild_id`, asked of Discord."""
    try:
        await (await _guild(ctx, guild_id)).fetch_member(user_id)
    except discord.NotFound:
        return False
    return True
