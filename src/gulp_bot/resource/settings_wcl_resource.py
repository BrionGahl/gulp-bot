"""`/settings wcl`: this server's Warcraft Logs settings: the API client, where reports come from, the
abilities tracked, the players left out and the abilities counted as consumables.

A group of its own because Discord allows a command at most 25 subcommands, and `/settings` had more than
that with these directly under it. `settings_resource.py` puts it under `/settings`.
"""

from __future__ import annotations

from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from ..core.discord_context import GulpContext, slash_context
from ..core.permission_checks import check_is_admin_or_moderator, prefix_check, slash_check
from ..schema.guild_settings_schema import ClearableSetting, WclCredentials, WclSource
from ..schema.wcl_settings_schema import ALL_CONSUMABLE_KINDS, ConsumableKind, IgnorePlayerOutcome, RegisterConsumableOutcome, TrackAbilityOutcome
from ..service import wcl_consumable_service, wcl_ignored_player_service, wcl_tracked_ability_service
from ..service.wcl_consumable_service import ids_of_kind
from .resource_support import choice_value, meta, reply, require_guild, subcommand_required
from .wcl_autocomplete import autocomplete_ignored_player, autocomplete_tracked_ability

# The largest ID Warcraft Logs uses: its IDs are signed 32-bit integers, and zero is never one.
MAX_WCL_ID = 2**31 - 1

U32 = app_commands.Range[int, 0, 2**32 - 1]

KIND_CHOICES = [app_commands.Choice(name=kind.display_name, value=kind.value) for kind in ALL_CONSUMABLE_KINDS]


def is_valid_wcl_id(value: int) -> bool:
    """The API's IDs are signed 32-bit integers, and zero is never one."""
    return 1 <= value <= MAX_WCL_ID


@commands.hybrid_group(name="wcl", description="Warcraft Logs settings: API client, report source, abilities, ignored players, consumables")
async def settings_wcl(ctx: GulpContext) -> None:
    await subcommand_required(ctx)


meta(settings_wcl, ephemeral=True)


def _sub(command: Any, help_text: str | None = None) -> None:
    meta(command, help_text=help_text, ephemeral=True)


# --- API client and report source ---


# Slash command only, so the secret is never typed into a channel as a normal message.
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
    await ctx.services.guild_settings.set_wcl_credentials(guild_id, WclCredentials(client_id, client_secret))
    await reply(ctx, f"{ClearableSetting.WCL_CLIENT.label} registered for this server.")


meta(wcl_client, help_text="Create one at warcraftlogs.com (Profile, then Manage Your Clients) and give its ID and secret.", ephemeral=True)
settings_wcl.app_command.add_command(wcl_client)


@settings_wcl.command(name="source", description="Register where this server's Warcraft Logs reports are found: the guild and the uploader")
@prefix_check(check_is_admin_or_moderator)
@app_commands.describe(guild="Warcraft Logs guild ID the reports are uploaded to", uploader="Warcraft Logs user ID of the account that uploads the logs")
async def wcl_source(ctx: GulpContext, guild: U32, uploader: U32) -> None:
    guild_id = require_guild(ctx)
    if not (is_valid_wcl_id(guild) and is_valid_wcl_id(uploader)):
        await reply(ctx, "Those aren't valid Warcraft Logs IDs. Both are positive whole numbers.")
        return
    await ctx.services.guild_settings.set_wcl_source(guild_id, WclSource(guild, uploader))
    await reply(ctx, f"{ClearableSetting.WCL_SOURCE.label} set to guild {guild}, uploader {uploader}.")


_sub(wcl_source, "Both are the numeric IDs Warcraft Logs uses. Only reports from this uploader are picked when `/wcl report` is run without a link.")


# --- Tracked abilities ---


@settings_wcl.command(name="mechanic_add", description="Track an ability in the Warcraft Logs overview, to see who took damage from it")
@prefix_check(check_is_admin_or_moderator)
@app_commands.describe(name="Name to show, e.g. Coalesced Venom", ability_id="The game's ability (spell) ID")
async def wcl_mechanic_add(ctx: GulpContext, name: str, ability_id: U32) -> None:
    guild_id = require_guild(ctx)
    name = name.strip()
    if not name or len(name) > wcl_tracked_ability_service.MAX_NAME_CHARS:
        await reply(ctx, f"The name has to be 1 to {wcl_tracked_ability_service.MAX_NAME_CHARS} characters.")
        return
    if not is_valid_wcl_id(ability_id):
        await reply(ctx, "That isn't a valid ability ID. It's a positive whole number.")
        return

    result = await ctx.services.tracked_abilities.track(guild_id, ability_id, name)
    match result.outcome:
        case TrackAbilityOutcome.ADDED:
            message = f"Tracking **{name}** (ability {ability_id})."
        case TrackAbilityOutcome.RENAMED:
            message = f"Ability {ability_id} is now called **{name}**."
        case TrackAbilityOutcome.NAME_TAKEN:
            message = f"Another ability (ID {result.other_ability_id}) is already called **{name}**. Pick a different name."
        case TrackAbilityOutcome.FULL:
            message = f"This server already tracks {wcl_tracked_ability_service.MAX_TRACKED} abilities. Remove one with `/settings wcl mechanic_remove` first."
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
    name = name.strip()
    if await ctx.services.tracked_abilities.untrack(guild_id, name):
        await reply(ctx, f"No longer tracking **{name}**.")
    else:
        await reply(ctx, f"No tracked ability is called **{name}**. See `/settings wcl mechanic_list`.")


_sub(wcl_mechanic_remove)
wcl_mechanic_remove.autocomplete("name")(autocomplete_tracked_ability)


@settings_wcl.command(name="mechanic_list", description="List the abilities tracked in the Warcraft Logs overview")
@prefix_check(check_is_admin_or_moderator)
async def wcl_mechanic_list(ctx: GulpContext) -> None:
    guild_id = require_guild(ctx)
    tracked = await ctx.services.tracked_abilities.list_tracked(guild_id)
    if not tracked:
        await reply(ctx, "No abilities are tracked. Add one with `/settings wcl mechanic_add`.")
        return
    lines = "\n".join(f"**{ability.name}** — ability {ability.ability_id}" for ability in tracked)
    await reply(ctx, f"Tracked abilities ({len(tracked)} of {wcl_tracked_ability_service.MAX_TRACKED}):\n{lines}")


_sub(wcl_mechanic_list)


# --- Ignored players ---


@settings_wcl.command(name="ignore_add", description="Leave a player out of the Warcraft Logs overview")
@prefix_check(check_is_admin_or_moderator)
@app_commands.describe(name="Character name, e.g. Tritonian")
async def wcl_ignore_add(ctx: GulpContext, name: str) -> None:
    guild_id = require_guild(ctx)
    name = name.strip()
    if not name or len(name) > wcl_ignored_player_service.MAX_NAME_CHARS:
        await reply(ctx, f"The name has to be 1 to {wcl_ignored_player_service.MAX_NAME_CHARS} characters.")
        return

    match await ctx.services.ignored_players.ignore(guild_id, name):
        case IgnorePlayerOutcome.ADDED:
            message = f"**{name}** is now left out of the Warcraft Logs overview."
        case IgnorePlayerOutcome.ALREADY_IGNORED:
            message = f"**{name}** is already ignored."
        case IgnorePlayerOutcome.FULL:
            message = f"This server already ignores {wcl_ignored_player_service.MAX_IGNORED} players. Remove one with `/settings wcl ignore_remove` first."
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
    name = name.strip()
    if await ctx.services.ignored_players.stop_ignoring(guild_id, name):
        await reply(ctx, f"**{name}** is no longer ignored.")
    else:
        await reply(ctx, f"**{name}** isn't ignored. See `/settings wcl ignore_list`.")


_sub(wcl_ignore_remove)
wcl_ignore_remove.autocomplete("name")(autocomplete_ignored_player)


@settings_wcl.command(name="ignore_list", description="List the players left out of the Warcraft Logs overview")
@prefix_check(check_is_admin_or_moderator)
async def wcl_ignore_list(ctx: GulpContext) -> None:
    guild_id = require_guild(ctx)
    ignored = await ctx.services.ignored_players.list_ignored(guild_id)
    if not ignored:
        await reply(ctx, "No players are ignored. Add one with `/settings wcl ignore_add`.")
        return
    await reply(ctx, f"Ignored players ({len(ignored)} of {wcl_ignored_player_service.MAX_IGNORED}): {', '.join(ignored)}")


_sub(wcl_ignore_list)


# --- Consumables ---


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
    chosen = ConsumableKind(value)
    if not is_valid_wcl_id(ability_id):
        await reply(ctx, "That isn't a valid ability ID. It's a positive whole number.")
        return

    result = await ctx.services.consumables.register(guild_id, chosen, ability_id)
    kind_name = chosen.display_name.lower()
    match result.outcome:
        case RegisterConsumableOutcome.ADDED:
            message = f"Ability {ability_id} now counts as a **{kind_name}**."
        case RegisterConsumableOutcome.ALREADY_REGISTERED:
            message = f"Ability {ability_id} already counts as a **{kind_name}**."
        case RegisterConsumableOutcome.OTHER_KIND if result.other_kind is not None:
            message = (
                f"Ability {ability_id} is registered as a **{result.other_kind.display_name.lower()}**. Remove it with `/settings wcl consumable_remove` first."
            )
        case _:
            message = (
                f"This server already has {wcl_consumable_service.MAX_PER_KIND} abilities registered as a {kind_name}. "
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
    kind = await ctx.services.consumables.unregister(guild_id, ability_id)
    if kind is not None:
        await reply(ctx, f"Ability {ability_id} no longer counts as a **{kind.display_name.lower()}**.")
    else:
        await reply(ctx, f"Ability {ability_id} isn't registered. See `/settings wcl consumable_list`.")


_sub(wcl_consumable_remove)


@settings_wcl.command(name="consumable_list", description="List the abilities counted as healthstones and health potions")
@prefix_check(check_is_admin_or_moderator)
async def wcl_consumable_list(ctx: GulpContext) -> None:
    guild_id = require_guild(ctx)
    registered = await ctx.services.consumables.list_registered(guild_id)
    if not registered:
        await reply(ctx, "No abilities are registered. Add one with `/settings wcl consumable_add`.")
        return
    lines = []
    for kind in ALL_CONSUMABLE_KINDS:
        ids = ids_of_kind(registered, kind)
        lines.append(f"**{kind.plural}**: {', '.join(map(str, ids)) if ids else 'none'}")
    await reply(ctx, "\n".join(lines))


_sub(wcl_consumable_list)
