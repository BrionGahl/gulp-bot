"""`/wcl`: Warcraft Logs overviews of raid nights. Administrators and moderators, and only in the raid notes
threads."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import discord
from discord import app_commands

from ..client.warcraft_logs_client import WarcraftLogsClient
from ..core.discord_context import NO_RAID_NOTES_CHANNEL, GulpContext, guild_context
from ..core.discord_helpers import NO_MENTIONS
from ..core.embed import Embed, to_discord_embed
from ..core.permission_checks import check_is_admin_or_moderator, slash_check
from ..core.text import same_name
from ..exception.warcraft_logs_exception import ReportRequestProblem, WarcraftLogsError
from ..mapper.warcraft_logs_embed_mapper import consumables_embed, damage_embed, overview_embed, replay_embed, window_damage_embed
from ..schema.guild_settings_schema import GuildSettings
from ..schema.wcl_report_schema import ReportTarget
from ..service.warcraft_logs_analysis_service import parse_pull_time, parse_report_target, resolve_ability
from .resource_support import meta, refuse
from .wcl_autocomplete import autocomplete_tracked_ability, autocomplete_tracked_enemy

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


@dataclass(frozen=True)
class WclRequest:
    """What a `/wcl` command has to go on once everything that doesn't need the network has been checked."""

    ctx: GulpContext
    guild_id: int
    settings: GuildSettings
    client: WarcraftLogsClient
    target: ReportTarget


async def start_request(interaction: discord.Interaction, report: str | None) -> WclRequest | None:
    """Checks that the command runs in a raid notes thread, that the server has API credentials and that the
    report asked for can be worked out. If not, tells only the officer who ran it and returns `None`.
    Everything here is checked before the command is deferred, so that a refusal is private rather than a
    public follow-up."""
    ctx, guild_id = await guild_context(interaction)
    settings = ctx.services.guild_settings.get(guild_id)

    raid_notes_channel_id = settings.raid_notes_channel_id
    if raid_notes_channel_id is None:
        await refuse(ctx, NO_RAID_NOTES_CHANNEL)
        return None
    if not await _in_thread_of(ctx, raid_notes_channel_id):
        await refuse(ctx, f"Warcraft Logs results are only posted in the weekly threads of <#{raid_notes_channel_id}>. Use the command in one of them.")
        return None
    if settings.wcl_credentials is None:
        await refuse(ctx, "No Warcraft Logs API client is registered for this server. An administrator or moderator can set one with `/settings wcl client`.")
        return None
    try:
        target = parse_report_target(report, settings)
    except ReportRequestProblem as problem:
        await refuse(ctx, str(problem))
        return None

    return WclRequest(ctx, guild_id, settings, ctx.services.warcraft_logs.client_for(guild_id, settings.wcl_credentials), target)


async def _in_thread_of(ctx: GulpContext, forum_id: int) -> bool:
    """Whether the command was used in a thread (a forum post) of `forum_id`."""
    channel: Any = ctx.channel
    if not isinstance(channel, discord.Thread):
        try:
            channel = await ctx.bot.fetch_channel(ctx.channel.id)
        except discord.HTTPException:
            return False
    return isinstance(channel, discord.Thread) and channel.parent_id == forum_id


async def resolve_report_code(request: WclRequest) -> str | None:
    """The code of the report asked for, looking up the newest one if that is what was asked. If that fails
    or finds nothing, says so in the channel and returns `None`."""
    try:
        code = await request.ctx.services.warcraft_logs.report_code_for(request.client, request.target)
    except WarcraftLogsError as e:
        await say_it_failed(request.ctx, e)
        return None
    if code is None:
        await say_quietly(request.ctx, "None of the guild's newest reports are from the registered uploader. Give a report link or code instead.")
    return code


async def say_it_failed(ctx: GulpContext, error: Exception) -> None:
    """Reports an API failure in the channel, since the command has already been deferred publicly."""
    log.error("Warcraft Logs request failed: %s", error)
    await say_quietly(ctx, f"Couldn't get that from Warcraft Logs. {error}")


async def post_from_report[T](request: WclRequest, fetch: Callable[[str], Awaitable[T]], to_embed: Callable[[T], Embed]) -> None:
    """What every `/wcl` command does once its own checks are done: defers (the API takes longer than Discord
    waits for a first response), works out the report, fetches what the command shows with `fetch(code)`,
    and posts it as an embed that pings nobody. A failure is said in the channel."""
    ctx = request.ctx
    await ctx.defer()
    code = await resolve_report_code(request)
    if code is None:
        return
    try:
        result = await fetch(code)
    except WarcraftLogsError as e:
        await say_it_failed(ctx, e)
        return
    await ctx.send(embed=to_discord_embed(to_embed(result)), allowed_mentions=NO_MENTIONS)


async def say_quietly(ctx: GulpContext, content: str) -> None:
    """Says something in the channel without pinging anyone, whatever the text holds: it may hold the API's
    own error text, or what an officer typed."""
    await ctx.send(content, allowed_mentions=NO_MENTIONS)


@wcl.command(name="report", description="Post the overview of a raid night's report: pulls, deaths and damage from tracked abilities")
@slash_check(check_is_admin_or_moderator)
@app_commands.describe(report=REPORT_DESCRIPTION)
async def report(interaction: discord.Interaction, report: str | None = None) -> None:
    request = await start_request(interaction, report)
    if request is None:
        return
    services = request.ctx.services
    tracked = await services.tracked_abilities.list_tracked(request.guild_id)
    ignored = await services.ignored_players.list_ignored(request.guild_id)
    await post_from_report(request, lambda code: services.warcraft_logs.overview(request.client, code, tracked, ignored), overview_embed)


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
@app_commands.autocomplete(ability=autocomplete_tracked_ability)
async def damage_taken(interaction: discord.Interaction, ability: str, boss: str | None = None, report: str | None = None) -> None:
    request = await start_request(interaction, report)
    if request is None:
        return
    ctx, services = request.ctx, request.ctx.services
    tracked = await services.tracked_abilities.list_tracked(request.guild_id)
    ignored = await services.ignored_players.list_ignored(request.guild_id)
    chosen = resolve_ability(tracked, ability)
    if chosen is None:
        await refuse(ctx, f"`{ability.strip()}` isn't a tracked ability or a spell ID. Track one with `/settings wcl mechanic_add`, or give its spell ID.")
        return
    await post_from_report(request, lambda code: services.warcraft_logs.damage_report(request.client, code, boss, chosen, ignored), damage_embed)


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
    request = await start_request(interaction, report)
    if request is None:
        return
    ctx, services = request.ctx, request.ctx.services
    registered = await services.consumables.list_registered(request.guild_id)
    if not registered:
        await refuse(ctx, "No healthstone or health potion abilities are registered for this server. Add them with `/settings wcl consumable_add`.")
        return
    ignored = await services.ignored_players.list_ignored(request.guild_id)
    await post_from_report(request, lambda code: services.warcraft_logs.consumables_report(request.client, code, boss, registered, ignored), consumables_embed)


meta(
    consumables,
    help_text=(
        "Every use counts, in every pull, including once the raid had started wiping: this is about what the raid consumed. The abilities that count "
        "are the ones registered with `/settings wcl consumable_add`. Only Mythic boss pulls are included. Without a report, the newest report from "
        "the uploader registered with `/settings wcl source` is used."
    ),
)


@wcl.command(name="damage_window", description="Post how much damage each player did to each tracked enemy in a moment of a pull")
@slash_check(check_is_admin_or_moderator)
@app_commands.describe(
    boss="The boss whose pull it is: a name, or part of one",
    start="Start of the window, from the start of the pull: 40, 40s or 0:40",
    end="End of the window, from the start of the pull: 60, 60s or 1:00",
    pull="Which pull of that boss, counting from 1; defaults to the last one",
    focus="A tracked enemy to set apart; the damage to the others is added together",
    boss_damage="Show boss against non-boss damage instead of the tracked enemies",
    report=REPORT_DESCRIPTION,
)
@app_commands.autocomplete(focus=autocomplete_tracked_enemy)
async def damage_window(
    interaction: discord.Interaction,
    boss: str,
    start: str,
    end: str,
    pull: app_commands.Range[int, 1, 999] | None = None,
    focus: str | None = None,
    boss_damage: bool = False,
    report: str | None = None,
) -> None:
    request = await start_request(interaction, report)
    if request is None:
        return
    ctx, services = request.ctx, request.ctx.services
    start_seconds, end_seconds = parse_pull_time(start), parse_pull_time(end)
    if start_seconds is None or end_seconds is None:
        wrong = start if start_seconds is None else end
        await refuse(ctx, f"`{wrong.strip()}` isn't a time into the pull. Give seconds (`40`, `40s`) or minutes and seconds (`0:40`).")
        return
    if end_seconds <= start_seconds:
        await refuse(ctx, "The end of the window has to be after its start.")
        return
    if boss_damage and focus is not None:
        await refuse(ctx, "`focus` and `boss_damage` are two different views. Pick one.")
        return
    ignored = await services.ignored_players.list_ignored(request.guild_id)
    if boss_damage:
        # Boss against non-boss damage, which needs no tracked enemies.
        await post_from_report(
            request,
            lambda code: services.warcraft_logs.window_boss_damage_report(request.client, code, boss, pull, start_seconds, end_seconds, ignored),
            window_damage_embed,
        )
        return

    enemies = await services.tracked_enemies.list_tracked(request.guild_id)
    if not enemies:
        await refuse(ctx, "No enemies are tracked on this server. Add them with `/settings wcl enemy_add`.")
        return
    if focus is not None:
        chosen = next((name for name in enemies if same_name(name, focus)), None)
        if chosen is None:
            await refuse(ctx, f"`{focus.strip()}` isn't a tracked enemy. See `/settings wcl enemy_list`.")
            return
        focus = chosen
    await post_from_report(
        request,
        lambda code: services.warcraft_logs.window_damage_report(request.client, code, boss, pull, start_seconds, end_seconds, enemies, ignored, focus),
        window_damage_embed,
    )


meta(
    damage_window,
    help_text=(
        "The times are counted from the start of the pull. Every enemy registered with `/settings wcl enemy_add` gets a ranking of the players "
        "who damaged it in that window, with the total each did; enemies with the same name count as one. With `focus`, one tracked enemy is set apart and the damage to the other tracked enemies "
        "is added together, in one ranking by the damage to the enemy set apart. With `boss_damage`, each player's damage is split instead into "
        "damage to the bosses, as Warcraft Logs counts them, and to everything else; no tracked enemies are needed. Only Mythic boss pulls are included. "
        "Without a report, the newest report from the uploader registered with `/settings wcl source` is used."
    ),
)


@wcl.command(name="replay", description="Post a replay link to the last wipe in a raid night's report")
@slash_check(check_is_admin_or_moderator)
@app_commands.describe(report=REPORT_DESCRIPTION)
async def replay(interaction: discord.Interaction, report: str | None = None) -> None:
    request = await start_request(interaction, report)
    if request is None:
        return
    await post_from_report(request, lambda code: request.ctx.services.warcraft_logs.last_wipe(request.client, code), replay_embed)


meta(
    replay,
    help_text=(
        "The last Mythic boss pull that was a wipe. The link starts 5 seconds before the first moment three players died within two seconds of "
        "each other. Without a report, the newest report from the uploader registered with `/settings wcl source` is used."
    ),
)

COMMANDS = [wcl]
