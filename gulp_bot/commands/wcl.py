"""`/wcl`: Warcraft Logs overviews of raid nights, for administrators and moderators."""

import logging
from dataclasses import dataclass
from typing import Any

import discord
from discord import app_commands

from .. import warcraftlogs, wcl_consumables, wcl_ignored_players, wcl_mechanics
from ..checks import check_is_admin_or_moderator, slash_check
from ..context import GulpContext, slash_context
from ..guild_settings import GuildSettings
from ..helper import NO_MENTIONS, embed_from_dict, role_ids
from ..warcraftlogs import Code, Target, WarcraftLogs, WclError, parse_target, resolve_ability
from .base import meta, refuse

log = logging.getLogger(__name__)

REPORT_DESCRIPTION = "Report link or code; defaults to the newest report from the registered uploader"


# Every subcommand carries the check: a slash subcommand of a group doesn't run the group's checks.
wcl = app_commands.Group(name="wcl", description="Warcraft Logs overviews of raid nights, for administrators and moderators", guild_only=True)
meta(
    wcl,
    category="Warcraft Logs",
    help_text=(
        "Only in the threads of the raid notes forum (`/settings raid_notes_channel`), where each week has its own; the commands refuse to run "
        "anywhere else. The overview of a raid night is also posted to that week's thread by itself, shortly after the raid's scheduled end."
    ),
)


def _interaction_is_admin_or_moderator(interaction: discord.Interaction) -> bool:
    """`checks.is_admin_or_moderator`, straight from an interaction: for autocomplete, which runs no checks."""
    guild_id = interaction.guild_id
    if guild_id is None:
        return False
    mod_role_id = interaction.client.guild_settings.get(guild_id).mod_role_id  # type: ignore[attr-defined]
    return interaction.permissions.administrator or (mod_role_id is not None and mod_role_id in role_ids(interaction.user))


async def autocomplete_ability(interaction: discord.Interaction, partial: str) -> list[app_commands.Choice[str]]:
    """The names of this server's tracked abilities that contain what has been typed so far. Autocomplete
    runs no checks, so officers are checked here."""
    names: list[str] = []
    if interaction.guild_id is not None and _interaction_is_admin_or_moderator(interaction):
        try:
            names = [ability.name for ability in await wcl_mechanics.list_tracked(interaction.client.db, interaction.guild_id)]  # type: ignore[attr-defined]
        except Exception:
            names = []
    partial = partial.lower()
    return [app_commands.Choice(name=name, value=name) for name in names if partial in name.lower()][:25]


async def autocomplete_ignored_player(interaction: discord.Interaction, partial: str) -> list[app_commands.Choice[str]]:
    """The ignored players whose names contain what has been typed so far; officers only, like
    `autocomplete_ability`."""
    names: list[str] = []
    if interaction.guild_id is not None and _interaction_is_admin_or_moderator(interaction):
        try:
            names = await wcl_ignored_players.list_ignored(interaction.client.db, interaction.guild_id)  # type: ignore[attr-defined]
        except Exception:
            names = []
    partial = partial.lower()
    return [app_commands.Choice(name=name, value=name) for name in names if partial in name.lower()][:25]


@wcl.command(name="report", description="Post the overview of a raid night's report: pulls, deaths and damage from tracked abilities")
@slash_check(check_is_admin_or_moderator)
@app_commands.describe(report=REPORT_DESCRIPTION)
async def report(interaction: discord.Interaction, report: str | None = None) -> None:
    ctx = await slash_context(interaction)
    ready = await _ready(ctx)
    if ready is None:
        return
    try:
        target = parse_target(report, ready.settings)
    except ValueError as problem:
        await refuse(ctx, str(problem))
        return
    tracked = await wcl_mechanics.list_tracked(ctx.bot.db, ready.guild_id)
    ignored = await wcl_ignored_players.list_ignored(ctx.bot.db, ready.guild_id)

    # The API takes a few seconds, longer than Discord waits for a first response.
    await ctx.defer()
    code = await _resolve_code(ctx, ready.warcraft_logs, target)
    if code is None:
        return
    try:
        overview = await ready.warcraft_logs.overview(code, tracked, ignored)
    except WclError as e:
        await _failed(ctx, e)
        return
    await ctx.send(embed=embed_from_dict(warcraftlogs.overview_embed(overview)))


meta(
    report,
    help_text=(
        "Only the first two deaths in each pull are counted, since the rest is the raid wiping, and damage taken is counted up to the same moment. "
        "Only Mythic boss pulls are included. The abilities shown are the ones registered with `/settings wcl mechanic_add`. Without a report, "
        "the newest report from the uploader registered with `/settings wcl source` is used."
    ),
)


@wcl.command(name="damage_taken", description="Post who took damage from one ability in a raid night's report")
@slash_check(check_is_admin_or_moderator)
@app_commands.describe(ability="A tracked ability, or a spell ID", boss="Only the pulls of the boss whose name contains this", report=REPORT_DESCRIPTION)
@app_commands.autocomplete(ability=autocomplete_ability)
async def damage_taken(interaction: discord.Interaction, ability: str, boss: str | None = None, report: str | None = None) -> None:
    ctx = await slash_context(interaction)
    ready = await _ready(ctx)
    if ready is None:
        return
    try:
        target = parse_target(report, ready.settings)
    except ValueError as problem:
        await refuse(ctx, str(problem))
        return
    tracked = await wcl_mechanics.list_tracked(ctx.bot.db, ready.guild_id)
    ignored = await wcl_ignored_players.list_ignored(ctx.bot.db, ready.guild_id)
    chosen = resolve_ability(tracked, ability)
    if chosen is None:
        await refuse(
            ctx,
            f"`{ability.strip()}` isn't a tracked ability or a spell ID. Track one with `/settings wcl mechanic_add`, or give its spell ID.",
        )
        return

    await ctx.defer()
    code = await _resolve_code(ctx, ready.warcraft_logs, target)
    if code is None:
        return
    try:
        damage = await ready.warcraft_logs.damage_report(code, boss, chosen, ignored)
    except WclError as e:
        await _failed(ctx, e)
        return
    await ctx.send(embed=embed_from_dict(warcraftlogs.damage_embed(damage)))


meta(
    damage_taken,
    help_text=(
        "The ability is one registered with `/settings wcl mechanic_add`, or any spell ID. Damage is counted up to the second death in each pull, "
        "since the rest is the raid wiping. Only Mythic boss pulls are included. Without a report, the newest report from the uploader registered "
        "with `/settings wcl source` is used."
    ),
)


@wcl.command(name="consumables", description="Post how many healthstones and health potions were used in a raid night's report")
@slash_check(check_is_admin_or_moderator)
@app_commands.describe(boss="Only the pulls of the boss whose name contains this", report=REPORT_DESCRIPTION)
async def consumables(interaction: discord.Interaction, boss: str | None = None, report: str | None = None) -> None:
    ctx = await slash_context(interaction)
    ready = await _ready(ctx)
    if ready is None:
        return
    try:
        target = parse_target(report, ready.settings)
    except ValueError as problem:
        await refuse(ctx, str(problem))
        return
    registered = await wcl_consumables.list_registered(ctx.bot.db, ready.guild_id)
    if not registered:
        await refuse(ctx, "No healthstone or health potion abilities are registered for this server. Add them with `/settings wcl consumable_add`.")
        return
    ignored = await wcl_ignored_players.list_ignored(ctx.bot.db, ready.guild_id)

    await ctx.defer()
    code = await _resolve_code(ctx, ready.warcraft_logs, target)
    if code is None:
        return
    try:
        used = await ready.warcraft_logs.consumables(code, boss, registered, ignored)
    except WclError as e:
        await _failed(ctx, e)
        return
    await ctx.send(embed=embed_from_dict(warcraftlogs.consumables_embed(used)))


meta(
    consumables,
    help_text=(
        "Every use counts, in every pull, including once the raid had started wiping: this is about what the raid consumed. The abilities that count "
        "are the ones registered with `/settings wcl consumable_add`. Only Mythic boss pulls are included. Without a report, the newest report from "
        "the uploader registered with `/settings wcl source` is used."
    ),
)


@wcl.command(name="replay", description="Post a replay link to the last wipe in a raid night's report")
@slash_check(check_is_admin_or_moderator)
@app_commands.describe(report=REPORT_DESCRIPTION)
async def replay(interaction: discord.Interaction, report: str | None = None) -> None:
    ctx = await slash_context(interaction)
    ready = await _ready(ctx)
    if ready is None:
        return
    try:
        target = parse_target(report, ready.settings)
    except ValueError as problem:
        await refuse(ctx, str(problem))
        return

    await ctx.defer()
    code = await _resolve_code(ctx, ready.warcraft_logs, target)
    if code is None:
        return
    try:
        wipe = await ready.warcraft_logs.last_wipe(code)
    except WclError as e:
        await _failed(ctx, e)
        return
    await ctx.send(embed=embed_from_dict(warcraftlogs.replay_embed(wipe)), allowed_mentions=NO_MENTIONS)


meta(
    replay,
    help_text=(
        "The last Mythic boss pull that was a wipe. The link starts 5 seconds before the first moment three players died within two seconds of "
        "each other. Without a report, the newest report from the uploader registered with `/settings wcl source` is used."
    ),
)


@dataclass(frozen=True)
class _Ready:
    """What the `/wcl` commands need once the checks that don't use the network have passed."""

    warcraft_logs: WarcraftLogs
    guild_id: int
    settings: GuildSettings


async def _ready(ctx: GulpContext) -> _Ready | None:
    """Checks that the command runs in a raid notes thread and that the server has API credentials. If not,
    tells only the officer who ran it and returns `None`. Everything here is checked before the command is
    deferred, so that a refusal is private rather than a public follow-up."""
    guild_id = ctx.guild_id
    if guild_id is None:
        return None
    settings = ctx.bot.guild_settings.get(guild_id)

    raid_notes_channel_id = settings.raid_notes_channel_id
    if raid_notes_channel_id is None:
        await refuse(ctx, "No raid notes channel is registered for this server. An administrator or moderator can set one with `/settings raid_notes_channel`.")
        return None
    if not await _in_thread_of(ctx, raid_notes_channel_id):
        await refuse(ctx, f"Warcraft Logs results are only posted in the weekly threads of <#{raid_notes_channel_id}>. Use the command in one of them.")
        return None
    if settings.wcl_credentials is None:
        await refuse(ctx, "No Warcraft Logs API client is registered for this server. An administrator or moderator can set one with `/settings wcl client`.")
        return None

    return _Ready(ctx.bot.warcraft_logs.for_guild(guild_id, settings.wcl_credentials), guild_id, settings)


async def _in_thread_of(ctx: GulpContext, forum_id: int) -> bool:
    """Whether the command was used in a thread (a forum post) of `forum_id`."""
    channel: Any = ctx.channel
    if not isinstance(channel, discord.Thread):
        try:
            channel = await ctx.bot.fetch_channel(ctx.channel.id)
        except discord.HTTPException:
            return False
    return isinstance(channel, discord.Thread) and channel.parent_id == forum_id


async def _resolve_code(ctx: GulpContext, client: WarcraftLogs, target: Target) -> str | None:
    """The code of the report asked for, looking up the newest one if that is what was asked. If that fails
    or finds nothing, says so in the channel and returns `None`."""
    if isinstance(target, Code):
        return target.code
    try:
        code = await client.latest_report_code(target.source)
    except WclError as e:
        await _failed(ctx, e)
        return None
    if code is None:
        await _say_quietly(ctx, "None of the guild's newest reports are from the registered uploader. Give a report link or code instead.")
    return code


async def _failed(ctx: GulpContext, error: Exception) -> None:
    """Reports an API failure in the channel, since the command has already been deferred publicly. The
    message holds the API's own error text, or what an officer typed (a boss that doesn't exist), so it is
    sent without pinging anyone."""
    log.error("Warcraft Logs request failed: %s", error)
    await _say_quietly(ctx, f"Couldn't get that from Warcraft Logs. {error}")


async def _say_quietly(ctx: GulpContext, content: str) -> None:
    """Says something in the channel without pinging anyone, whatever the text holds."""
    await ctx.send(content, allowed_mentions=NO_MENTIONS)
