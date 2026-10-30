"""`/ai`: control the AI features: switch them off, change their model, see what they have used. Developers only."""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable
from dataclasses import dataclass

import discord
from discord import app_commands

from ..core.discord_context import ONLY_FOR_DEVELOPERS, GulpContext, guild_context, services_of
from ..core.permission_checks import check_is_developer, interaction_is_developer, is_developer, slash_check
from ..exception.ai_exception import ModelNameProblem
from ..mapper.ai_admin_mapper import check_text, status_text
from ..mapper.ai_usage_mapper import describe_think, usage_lines
from ..schema.ai_schema import CheckOk, Unreachable
from ..schema.guild_settings_schema import AiSettings, ThinkSetting
from ..service.ai_diagnostics_service import model_finding, role_findings, state_findings
from ..service.ai_model_catalog_service import normalize_model_name
from ..service.ai_service import AiService
from .resource_support import filter_choices, meta, reply, reply_in_chunks

log = logging.getLogger(__name__)

# Whether the model thinks before it answers. "configured" is stored as nothing (`LOCAL_MODEL_THINK`).
THINK_CHOICES = [
    app_commands.Choice(name="As configured", value="configured"),
    app_commands.Choice(name="On", value=ThinkSetting.ON.value),
    app_commands.Choice(name="Off", value=ThinkSetting.OFF.value),
    app_commands.Choice(name="Left to the model (send nothing)", value=ThinkSetting.DEFAULT.value),
]


# Every subcommand carries the check, and checks the role again for itself in `start_developer_command`,
# since these are the commands that can switch the AI features off and change the model they run.
ai_group = app_commands.Group(
    name="ai", description="Control the AI features: switch them off, change the model, see their usage. Developers only.", guild_only=True
)
meta(
    ai_group,
    category="AI",
    help_text=(
        "Only members with the developer role (`/settings dev_role`) can use these commands, and every use is written to the bot's log with who "
        "ran it. What is switched here applies only to the server the command is used in, and is kept when the bot restarts."
    ),
)


@dataclass(frozen=True)
class DeveloperCommand:
    """A `/ai` subcommand that may go ahead: who ran it, where, and the AI features it is about."""

    ctx: GulpContext
    guild_id: int
    ai: AiService


async def start_developer_command(interaction: discord.Interaction, action: str) -> DeveloperCommand | None:
    """Confirms the invoker holds the developer role (the check has already said so, but these commands are
    too powerful to rely on that alone), writes the use to the log, and hands back what the command needs.
    Tells the invoker and returns `None` if there is nothing to do."""
    ctx, guild_id = await guild_context(interaction)
    if not is_developer(ctx, guild_id):
        await reply(ctx, ONLY_FOR_DEVELOPERS)
        return None

    log.info("audit: %s (%s) used /ai %s in guild %s", ctx.author.id, ctx.author.name, action, guild_id)
    return DeveloperCommand(ctx, guild_id, ctx.services.ai)


async def change_ai_settings(command: DeveloperCommand, what: str, edit: Callable[[AiSettings], object]) -> AiSettings:
    """Changes how the AI features are switched in the guild, keeping the rest as it was, and writes what
    changed to the log. Returns the settings as they are now."""
    guild_settings = command.ctx.services.guild_settings
    settings = dataclasses.replace(guild_settings.get(command.guild_id).ai)
    edit(settings)
    await guild_settings.set_ai(command.guild_id, settings)
    author = command.ctx.author
    log.info("audit: %s (%s) changed the AI features in guild %s: %s", author.id, author.name, command.guild_id, what)
    return settings


def model_names(names: list[str]) -> str:
    """Model names for a message: `a`, `b`, or "none"."""
    return ", ".join(f"`{name}`" for name in names) if names else "none"


@ai_group.command(name="status", description="Show the AI features' state, model, thinking, today's use and latest failures")
@slash_check(check_is_developer)
async def ai_status(interaction: discord.Interaction) -> None:
    command = await start_developer_command(interaction, "status")
    if command is None:
        return
    ai = command.ai
    settings = command.ctx.services.guild_settings.get(command.guild_id).ai
    answered, limit = ai.daily_usage()
    failure_total, failures = ai.failure_report()
    text = status_text(
        settings, ai.resolve_model(settings), ai.configured_model, ai.resolve_think(settings), ai.configured_think, answered, limit, failure_total, failures
    )
    await reply_in_chunks(command.ctx, text)


@ai_group.command(name="on", description="Turn the AI features on")
@slash_check(check_is_developer)
async def ai_on(interaction: discord.Interaction) -> None:
    command = await start_developer_command(interaction, "on")
    if command is None:
        return
    settings = await change_ai_settings(command, "switched on", lambda ai: setattr(ai, "disabled", False))
    note = " They are still in maintenance mode, so only Officers and Developers can use them (`/ai maintenance`)." if settings.maintenance else ""
    await reply_in_chunks(command.ctx, f"The AI features are **on**.{note}")


@ai_group.command(name="off", description="Turn the AI features off for everybody")
@slash_check(check_is_developer)
async def ai_off(interaction: discord.Interaction) -> None:
    command = await start_developer_command(interaction, "off")
    if command is None:
        return
    await change_ai_settings(command, "switched off", lambda ai: setattr(ai, "disabled", True))
    await reply_in_chunks(command.ctx, "The AI features are **off**: nobody can use them, whatever their role. `/ai on` turns them back on.")


@ai_group.command(name="usage", description="Show the tokens used")
@slash_check(check_is_developer)
async def ai_usage(interaction: discord.Interaction) -> None:
    command = await start_developer_command(interaction, "usage")
    if command is None:
        return
    report = command.ai.usage_report()
    await reply_in_chunks(
        command.ctx,
        f"**Last 24 hours**\n{usage_lines(report.last_24h)}\n\n**Since the bot started**\n{usage_lines(report.since_start)}\n\n"
        "The counts start again from zero when the bot restarts.",
    )


async def autocomplete_model(interaction: discord.Interaction, partial: str) -> list[app_commands.Choice[str]]:
    """The models offered while a developer types one: the ones installed on the server. Autocomplete runs
    no checks, so developers are checked here."""
    ai: AiService = services_of(interaction).ai
    models: list[str] = []
    if interaction_is_developer(interaction):
        models = await ai.installed_models()
    return filter_choices(models, partial)


@ai_group.command(name="model", description="Change the model the AI features use, or go back to the configured one")
@slash_check(check_is_developer)
@app_commands.describe(name="A model name, or `default` for the one the bot was configured with")
@app_commands.autocomplete(name=autocomplete_model)
async def ai_model(interaction: discord.Interaction, name: str) -> None:
    command = await start_developer_command(interaction, "model")
    if command is None:
        return
    ctx, ai = command.ctx, command.ai

    try:
        model = normalize_model_name(name)
    except ModelNameProblem as problem:
        await reply_in_chunks(ctx, str(problem))
        return
    if model is None:
        await change_ai_settings(command, "model back to the configured one", lambda settings: setattr(settings, "model", None))
        await reply_in_chunks(ctx, f"The model is back to the configured `{ai.configured_model}`.")
        return

    # The server has to have the model, or every answer would fail until somebody noticed.
    await ctx.defer(ephemeral=True)
    check = await ai.check_model(model)
    if isinstance(check, CheckOk) and not check.model_listed:
        installed = model_names(await ai.installed_models())
        await reply_in_chunks(
            ctx,
            f"The local model server doesn't have `{model}`, so nothing was changed. Install it there with `ollama pull {model}`. Installed: {installed}.",
        )
        return
    if isinstance(check, Unreachable):
        await reply_in_chunks(
            ctx, f"The local model server couldn't be reached to check the model ({check.problem}), so nothing was changed. Try again in a moment."
        )
        return

    await change_ai_settings(command, f"model set to `{model}`", lambda settings: setattr(settings, "model", model))
    await reply_in_chunks(ctx, f"The model is now `{model}`, from the next request on.")


@ai_group.command(name="think", description="Turn the model's thinking on or off. On answers slower, but follows instructions better")
@slash_check(check_is_developer)
@app_commands.describe(mode="On, off, left to the model, or as configured")
@app_commands.choices(mode=THINK_CHOICES)
async def ai_think(interaction: discord.Interaction, mode: str) -> None:
    command = await start_developer_command(interaction, "think")
    if command is None:
        return
    ctx, ai = command.ctx, command.ai
    stored = None if mode == "configured" else ThinkSetting(mode)
    settings = await change_ai_settings(
        command, f"thinking set to {'configured' if stored is None else stored.value}", lambda settings: setattr(settings, "think", stored)
    )

    now = describe_think(ai.resolve_think(settings))
    message = f"Thinking is back to the configured setting: {now}." if stored is None else f"Thinking is now {now}."
    await reply_in_chunks(
        ctx,
        f"{message} It applies from the next request on. With thinking on, answers take longer: the model writes out its reasoning first, which isn't shown.",
    )


@ai_group.command(name="maintenance", description="Turn maintenance mode on or off: only Officers and Developers can use the AI features")
@slash_check(check_is_developer)
@app_commands.describe(enabled="True to turn maintenance mode on, false to turn it off")
async def ai_maintenance(interaction: discord.Interaction, enabled: bool) -> None:
    command = await start_developer_command(interaction, "maintenance")
    if command is None:
        return
    settings = await change_ai_settings(command, "maintenance mode on" if enabled else "maintenance mode off", lambda ai: setattr(ai, "maintenance", enabled))

    if enabled and not settings.disabled:
        message = "Maintenance mode is **on**: only Officers and Developers can use the AI features."
    elif enabled:
        message = "Maintenance mode is **on**, but the AI features are off, so nobody can use them. `/ai on` turns them on."
    elif not settings.disabled:
        message = "Maintenance mode is **off**: all raiders can use the AI features again."
    else:
        message = "Maintenance mode is **off**, but the AI features are still off. `/ai on` turns them on."
    await reply_in_chunks(command.ctx, message)


@ai_group.command(name="check", description="Check the registered roles, the model server and the model")
@slash_check(check_is_developer)
async def ai_check(interaction: discord.Interaction) -> None:
    command = await start_developer_command(interaction, "check")
    if command is None:
        return
    ctx, ai, guild_id = command.ctx, command.ai, command.guild_id
    await ctx.defer(ephemeral=True)

    settings = ctx.services.guild_settings.get(guild_id)
    model = ai.resolve_model(settings.ai)
    existing = await ctx.services.guild_validation.existing_role_ids(guild_id)
    answered, limit = ai.daily_usage()
    failure_total, failures = ai.failure_report()
    findings = [
        *role_findings(settings, guild_id, set(existing)),
        model_finding(model, await ai.check_model(model)),
        *state_findings(settings, answered, limit, failure_total, failures),
    ]
    await reply_in_chunks(ctx, check_text(findings))


for _command in ai_group.commands:
    meta(_command, ephemeral=True)

COMMANDS = [ai_group]
