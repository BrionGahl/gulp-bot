"""`/settings`: what this server has registered with the bot. Administrators and moderators.

The Warcraft Logs settings are a group of their own, `/settings wcl` (`settings_wcl_resource.py`).
"""

from __future__ import annotations

from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from ..core.discord_context import GulpContext, slash_context
from ..core.embed import command_embed, to_discord_embed
from ..core.permission_checks import check_is_admin_or_moderator, check_is_administrator, prefix_check, slash_check
from ..core.schedule_parsing import next_fire_discord_timestamp, parse_user_schedule
from ..exception.schedule_exception import ScheduleProblem
from ..mapper.guild_settings_mapper import settings_overview_embed
from ..mapper.officer_channel_mapper import officer_channel_list_description
from ..schema.guild_settings_schema import ClearableSetting, GuildIdSetting, RaidVoice, WowUtilsCredentials
from ..service.guild_validation_service import TEXT_CHANNEL_TYPES, VOICE_CHANNEL_TYPES
from ..service.raid_notes_service import DEFAULT_RAID_NOTES_CRON
from .resource_support import choice_value, meta, reply, require_guild, subcommand_required
from .settings_wcl_resource import settings_wcl

CLEAR_CHOICES = [app_commands.Choice(name=setting.label, value=setting.value) for setting in ClearableSetting]

NOT_A_MEMBER = "<@{user_id}> isn't a member of this server, so they can't be registered here."


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


async def register_role(ctx: GulpContext, setting: GuildIdSetting, role_id: int) -> None:
    """Registers `role_id` for `setting` after confirming, against Discord, that the role belongs to the
    server the command was used in."""
    guild_id = require_guild(ctx)
    problem = await ctx.services.guild_validation.role_problem(guild_id, role_id)
    if problem is not None:
        await reply(ctx, problem)
        return
    await ctx.services.guild_settings.set_id_setting(guild_id, setting, role_id)
    await reply(ctx, f"{setting.value} set to <@&{role_id}>.")


async def register_channel(ctx: GulpContext, setting: GuildIdSetting, channel_id: int, allowed: list[discord.ChannelType], expected: str) -> None:
    """Registers `channel_id` for `setting` after confirming, against Discord, that the channel belongs to
    the server the command was used in and is one of the `allowed` types."""
    guild_id = require_guild(ctx)
    problem = await ctx.services.guild_validation.channel_problem(guild_id, channel_id, allowed, expected)
    if problem is not None:
        await reply(ctx, problem)
        return
    await ctx.services.guild_settings.set_id_setting(guild_id, setting, channel_id)
    await reply(ctx, f"{setting.value} set to <#{channel_id}>.")


# --- Overview ---


@settings.command(name="show", description="Show everything registered for this server")
@prefix_check(check_is_admin_or_moderator)
async def show(ctx: GulpContext) -> None:
    guild_id = require_guild(ctx)
    services = ctx.services
    embed = settings_overview_embed(
        services.config.bot_name,
        services.guild_settings.get(guild_id),
        DEFAULT_RAID_NOTES_CRON,
        tracked_abilities=await services.tracked_abilities.list_tracked(guild_id),
        ignored_players=len(await services.ignored_players.list_ignored(guild_id)),
        consumables=await services.consumables.list_registered(guild_id),
    )
    await ctx.send(embed=to_discord_embed(embed))


_sub(show)


# --- Roles ---


@settings.command(name="mod_role", description="Register the moderator (officer) role for this server. Administrators only.")
@prefix_check(check_is_admin_or_moderator)
@prefix_check(check_is_administrator)
@app_commands.describe(role="Role that grants moderator commands")
async def mod_role(ctx: GulpContext, role: discord.Role) -> None:
    await register_role(ctx, GuildIdSetting.MOD_ROLE, role.id)


_sub(mod_role)


@settings.command(name="dev_role", description="Register the developer role for this server. Administrators only.")
@prefix_check(check_is_admin_or_moderator)
@prefix_check(check_is_administrator)
@app_commands.describe(role="Role held by the bot's developers")
async def dev_role(ctx: GulpContext, role: discord.Role) -> None:
    guild_id = require_guild(ctx)
    # The role would otherwise hand developer powers to every moderator, raider or trial.
    current = ctx.services.guild_settings.get(guild_id)
    if role.id in (current.mod_role_id, current.raider_role_id, current.trial_role_id):
        await reply(ctx, "That role is registered as the moderator, raider or trial role. Pick a role that only developers hold.")
        return
    await register_role(ctx, GuildIdSetting.DEV_ROLE, role.id)


_sub(
    dev_role,
    "Developers run the `/ai` commands and rank above moderators for the AI assistant. Pick a role that only developers hold: it can't be the moderator, raider or trial role.",
)


@settings.command(name="raider_role", description="Register the raider role for this server")
@prefix_check(check_is_admin_or_moderator)
@app_commands.describe(role="Role held by raiders")
async def raider_role(ctx: GulpContext, role: discord.Role) -> None:
    await register_role(ctx, GuildIdSetting.RAIDER_ROLE, role.id)


_sub(raider_role)


@settings.command(name="trial_role", description="Register the trial role for this server")
@prefix_check(check_is_admin_or_moderator)
@app_commands.describe(role="Role held by trial raiders")
async def trial_role(ctx: GulpContext, role: discord.Role) -> None:
    await register_role(ctx, GuildIdSetting.TRIAL_ROLE, role.id)


_sub(trial_role)


# --- Personal officer channels ---


@settings.command(name="officer_category", description="Register the category that personal officer channels are created under")
@prefix_check(check_is_admin_or_moderator)
@app_commands.describe(category="Category for personal officer channels")
async def officer_category(ctx: GulpContext, category: discord.CategoryChannel) -> None:
    await register_channel(ctx, GuildIdSetting.PERSONAL_OFFICER_CATEGORY, category.id, [discord.ChannelType.category], "a category")


_sub(officer_category)


@settings.command(name="officer_channel_add", description="Register a personal officer channel that already exists for a member")
@prefix_check(check_is_admin_or_moderator)
@app_commands.describe(user="Member the channel belongs to", channel="Their existing personal officer channel")
async def officer_channel_add(ctx: GulpContext, user: discord.User, channel: discord.TextChannel) -> None:
    guild_id = require_guild(ctx)
    validation = ctx.services.guild_validation
    if not await validation.is_member(guild_id, user.id):
        await reply(ctx, NOT_A_MEMBER.format(user_id=user.id))
        return
    problem = await validation.channel_problem(guild_id, channel.id, [discord.ChannelType.text], "a text channel")
    if problem is not None:
        await reply(ctx, problem)
        return

    await ctx.services.officer_channels.register_existing(guild_id, user.id, channel.id)
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
    if await ctx.services.officer_channels.forget(guild_id, user.id):
        await reply(ctx, f"<@{user.id}> no longer has a registered personal officer channel. The channel itself was not deleted.")
    else:
        await reply(ctx, f"<@{user.id}> has no registered personal officer channel in this server.")


_sub(officer_channel_remove)


@settings.command(name="officer_channel_list", description="List the members who have a registered personal officer channel")
@prefix_check(check_is_admin_or_moderator)
async def officer_channel_list(ctx: GulpContext) -> None:
    guild_id = require_guild(ctx)
    registered = await ctx.services.officer_channels.list_registered(guild_id)
    if not registered:
        await reply(ctx, "No personal officer channels are registered for this server.")
        return
    embed = command_embed(
        ctx.services.config.bot_name, title=f"Personal Officer Channels ({len(registered)})", description=officer_channel_list_description(registered)
    )
    await ctx.send(embed=to_discord_embed(embed))


_sub(officer_channel_list)


# --- Raid notes and attendance ---


@settings.command(name="raid_notes_channel", description="Register the forum channel that weekly raid notes threads are posted in")
@prefix_check(check_is_admin_or_moderator)
@app_commands.describe(channel="Forum channel for weekly raid notes")
async def raid_notes_channel(ctx: GulpContext, channel: discord.ForumChannel) -> None:
    await register_channel(ctx, GuildIdSetting.RAID_NOTES_CHANNEL, channel.id, [discord.ChannelType.forum], "a forum channel")


_sub(raid_notes_channel)


@settings.command(name="raid_notes_cron", description="Set when the raid notes post is created, as a quartz-style cron expression (Eastern time)")
@prefix_check(check_is_admin_or_moderator)
@app_commands.describe(expression="sec min hour day-of-month month day-of-week [year], e.g. 0 0 9 * * Fri *")
async def raid_notes_cron(ctx: GulpContext, *, expression: str) -> None:
    guild_id = require_guild(ctx)
    try:
        schedule = parse_user_schedule(expression.strip())
    except ScheduleProblem as problem:
        await reply(ctx, str(problem))
        return

    await ctx.services.guild_settings.set_raid_notes_schedule(guild_id, schedule)
    # `parse_user_schedule` only accepts schedules with an upcoming fire time.
    await reply(ctx, f"{ClearableSetting.RAID_NOTES_CRON.label} set to `{schedule.source}`. Next post: {next_fire_discord_timestamp(schedule)}.")


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
    problem = await ctx.services.guild_validation.channel_problem(guild_id, channel.id, VOICE_CHANNEL_TYPES, "a voice channel")
    if problem is not None:
        await reply(ctx, problem)
        return
    try:
        start_schedule = parse_user_schedule(start.strip())
    except ScheduleProblem as e:
        await reply(ctx, f"Start: {e}")
        return
    try:
        end_schedule = parse_user_schedule(end.strip())
    except ScheduleProblem as e:
        await reply(ctx, f"End: {e}")
        return

    # `parse_user_schedule` only accepts schedules with an upcoming fire time.
    message = (
        f"{ClearableSetting.RAID_VOICE.label} set to <#{channel.id}>. "
        f"Next start: {next_fire_discord_timestamp(start_schedule)}. Next end: {next_fire_discord_timestamp(end_schedule)}."
    )
    if ctx.services.guild_settings.get(guild_id).raid_notes_channel_id is None:
        message += (
            "\nAttendance is posted in the week's raid notes thread, and no raid notes channel is registered yet; set one with `/settings raid_notes_channel`."
        )

    await ctx.services.guild_settings.set_raid_voice(guild_id, RaidVoice(channel.id, start_schedule, end_schedule))
    await reply(ctx, message)


_sub(raid_voice, "Both schedules are quartz-style cron expressions. Through the prefix form, each one has to be wrapped in quotes, since they contain spaces.")


# --- Delete-and-replay and clips channels ---


@settings.command(name="replay_add", description="Add a member whose self-deleted messages the bot reposts")
@prefix_check(check_is_admin_or_moderator)
@app_commands.describe(user="Member to replay deleted messages for")
async def replay_add(ctx: GulpContext, user: discord.User) -> None:
    guild_id = require_guild(ctx)
    if not await ctx.services.guild_validation.is_member(guild_id, user.id):
        await reply(ctx, NOT_A_MEMBER.format(user_id=user.id))
        return
    await ctx.services.guild_settings.add_replay_user(guild_id, user.id)
    await reply(ctx, f"<@{user.id}> is now a replay user.")


_sub(replay_add)


@settings.command(name="replay_remove", description="Stop reposting a member's self-deleted messages")
@prefix_check(check_is_admin_or_moderator)
@app_commands.describe(user="Replay user to remove")
async def replay_remove(ctx: GulpContext, user: discord.User) -> None:
    guild_id = require_guild(ctx)
    # No membership check: the member may have left the server since they were added.
    if await ctx.services.guild_settings.remove_replay_user(guild_id, user.id):
        await reply(ctx, f"<@{user.id}> is no longer a replay user.")
    else:
        await reply(ctx, f"<@{user.id}> isn't a replay user in this server.")


_sub(replay_remove)


@settings.command(name="clips_add", description="Restrict a channel to messages that contain a video")
@prefix_check(check_is_admin_or_moderator)
@app_commands.describe(channel="Channel to restrict to video-only messages")
async def clips_add(ctx: GulpContext, channel: discord.TextChannel) -> None:
    guild_id = require_guild(ctx)
    problem = await ctx.services.guild_validation.channel_problem(guild_id, channel.id, TEXT_CHANNEL_TYPES, "a text channel")
    if problem is not None:
        await reply(ctx, problem)
        return
    await ctx.services.guild_settings.add_clips_channel(guild_id, channel.id)
    await reply(ctx, f"<#{channel.id}> is now a clips channel.")


_sub(clips_add)


@settings.command(name="clips_remove", description="Stop restricting a channel to video-only messages")
@prefix_check(check_is_admin_or_moderator)
@app_commands.describe(channel="Clips channel to stop restricting")
async def clips_remove(ctx: GulpContext, channel: discord.abc.GuildChannel) -> None:
    guild_id = require_guild(ctx)
    # No ownership check: this only ever removes a row registered under this guild, and the channel may have
    # been deleted since it was added.
    if await ctx.services.guild_settings.remove_clips_channel(guild_id, channel.id):
        await reply(ctx, f"<#{channel.id}> is no longer a clips channel.")
    else:
        await reply(ctx, f"<#{channel.id}> isn't registered as a clips channel in this server.")


_sub(clips_remove)


# --- Credentials: slash commands only, so they are never typed into a channel as a normal message ---


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
    await ctx.services.guild_settings.set_bart_token(guild_id, token)
    await reply(ctx, f"{ClearableSetting.BART_TOKEN.label} registered for this server.")


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
    await ctx.services.guild_settings.set_wowutils(guild_id, WowUtilsCredentials(token, group_id))
    await reply(ctx, f"{ClearableSetting.WOWUTILS.label} registered for this server.")


meta(wowutils, ephemeral=True)
settings.app_command.add_command(wowutils)


# --- Warcraft Logs ---

settings.add_command(settings_wcl)


# --- Clearing ---


@settings.command(name="clear", description="Clear one of this server's registered settings")
@prefix_check(check_is_admin_or_moderator)
@app_commands.describe(setting="Setting to clear")
@app_commands.choices(setting=CLEAR_CHOICES)
async def clear(ctx: GulpContext, setting: str) -> None:
    guild_id = require_guild(ctx)
    value = choice_value(setting, CLEAR_CHOICES)
    if value is None:
        await reply(ctx, f"`{setting}` isn't one of: {', '.join(choice.name for choice in CLEAR_CHOICES)}.")
        return
    target = ClearableSetting(value)

    # Same restriction as `mod_role` and `dev_role`: clearing one of these roles is changing it.
    if target.administrators_only and not await check_is_administrator(ctx):
        return

    await ctx.services.guild_settings.clear(guild_id, target)
    await reply(ctx, f"{target.label} cleared.")


_sub(clear)

COMMANDS = [settings]
