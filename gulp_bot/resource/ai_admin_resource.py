"""`/ai`: control the AI assistant: switch it off, change its model, see what it costs. Developers only."""

from __future__ import annotations

import dataclasses
import enum
import logging
from collections.abc import Callable
from dataclasses import dataclass

import discord
from discord import app_commands

from ..core.discord_context import GulpContext, slash_context
from ..core.permission_checks import check_is_developer, interaction_is_developer, is_developer, slash_check
from ..exception.ai_exception import ModelNameProblem
from ..mapper.ai_usage_mapper import describe_effort, format_price, usage_lines
from ..schema.ai_schema import CheckOk, KeyRejected, Unreachable
from ..schema.guild_settings_schema import AiSettings
from ..service.ai_assistant_service import AiAssistantService
from ..service.ai_model_catalog_service import KNOWN_MODELS, normalize_model_name, price_per_million
from .resource_support import filter_choices, meta, reply_in_chunks

log = logging.getLogger(__name__)

# How much effort the model puts into an answer. "configured" is stored as nothing (the configured effort),
# "off" as none sent at all.
EFFORT_CHOICES = [
    app_commands.Choice(name="As configured", value="configured"),
    app_commands.Choice(name="Off (send none)", value="off"),
    app_commands.Choice(name="Low", value="low"),
    app_commands.Choice(name="Medium", value="medium"),
    app_commands.Choice(name="High", value="high"),
]


class Finding(enum.Enum):
    """How a line of `/ai check` reads."""

    GOOD = "✅"
    WARNING = "⚠️"
    BAD = "❌"


# Every subcommand carries the check, and checks the role again for itself in `start_developer_command`,
# since these are the commands that can silence the assistant and change what it costs.
ai_group = app_commands.Group(
    name="ai", description="Control the AI assistant: switch it off, change its model, see what it costs. Developers only.", guild_only=True
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
    """A `/ai` subcommand that may go ahead: who ran it, where, and the assistant it is about."""

    ctx: GulpContext
    guild_id: int
    ai: AiAssistantService


async def start_developer_command(interaction: discord.Interaction, action: str) -> DeveloperCommand | None:
    """Confirms the invoker holds the developer role (the check has already said so, but these commands are
    too powerful to rely on that alone), writes the use to the log, and hands back what the command needs.
    Tells the invoker and returns `None` if there is nothing to do."""
    ctx = await slash_context(interaction)
    guild_id = ctx.guild_id
    if guild_id is None:
        return None
    if not is_developer(ctx, guild_id):
        await reply_in_chunks(ctx, "This command is only available to developers.")
        return None

    log.info("audit: %s (%s) used /ai %s in guild %s", ctx.author.id, ctx.author.name, action, guild_id)
    ai = ctx.services.ai
    if ai is None:
        await reply_in_chunks(ctx, "The AI assistant isn't set up for this bot: there is no `ANTHROPIC_API_KEY`, and `AI_BACKEND` isn't `local`.")
        return None
    return DeveloperCommand(ctx, guild_id, ai)


async def change_ai_settings(command: DeveloperCommand, what: str, edit: Callable[[AiSettings], object]) -> AiSettings:
    """Changes how the assistant is switched in the guild, keeping the rest as it was, and writes what
    changed to the log. Returns the settings as they are now."""
    guild_settings = command.ctx.services.guild_settings
    settings = dataclasses.replace(guild_settings.get(command.guild_id).ai)
    edit(settings)
    await guild_settings.set_ai(command.guild_id, settings)
    author = command.ctx.author
    log.info("audit: %s (%s) changed the AI assistant in guild %s: %s", author.id, author.name, command.guild_id, what)
    return settings


def model_names(names: list[str]) -> str:
    """Model names for a message: `a`, `b`, or "none"."""
    return ", ".join(f"`{name}`" for name in names) if names else "none"


@ai_group.command(name="status", description="Show the assistant's state, model, effort, today's use and latest API failures")
@slash_check(check_is_developer)
async def ai_status(interaction: discord.Interaction) -> None:
    command = await start_developer_command(interaction, "status")
    if command is None:
        return
    ai = command.ai
    settings = command.ctx.services.guild_settings.get(command.guild_id).ai
    model, effort = ai.resolve(settings)

    if settings.disabled:
        state = "**off**: it answers nobody"
    elif settings.maintenance:
        state = "**on, in maintenance mode**: it answers only Officers and Developers"
    else:
        state = "**on**"
    model_note = f"set with `/ai model`; the bot was configured with `{ai.configured_model}`" if settings.model is not None else "as configured"
    if ai.is_local:
        effort_line = "not used by a local model"
    else:
        note = f"set with `/ai effort`; the bot was configured with {describe_effort(ai.configured_effort)}" if settings.effort is not None else "as configured"
        effort_line = f"{describe_effort(effort)} ({note})"
    served = "runs locally, through Ollama" if ai.is_local else "through the Anthropic API"
    answered, limit = ai.daily_usage()
    failure_total, failures = ai.failure_report()

    text = (
        f"**AI assistant**\nState: {state}\nModel: `{model}`, {served} ({model_note})\nEffort: {effort_line}\n"
        f"Questions answered in the last 24 hours: {answered} of {limit}\n"
    )
    if not failures:
        text += "No API failures since the bot started."
    else:
        text += f"API failures since the bot started: {failure_total}. The latest:"
        for failure in failures:
            text += f"\n• <t:{failure.at_unix}:R>: {failure.detail}"
    await reply_in_chunks(command.ctx, text)


@ai_group.command(name="on", description="Turn the AI assistant on")
@slash_check(check_is_developer)
async def ai_on(interaction: discord.Interaction) -> None:
    command = await start_developer_command(interaction, "on")
    if command is None:
        return
    settings = await change_ai_settings(command, "switched on", lambda ai: setattr(ai, "disabled", False))
    note = " It is still in maintenance mode, so it answers only Officers and Developers (`/ai maintenance`)." if settings.maintenance else ""
    await reply_in_chunks(command.ctx, f"The AI assistant is **on**.{note}")


@ai_group.command(name="off", description="Turn the AI assistant off for everybody")
@slash_check(check_is_developer)
async def ai_off(interaction: discord.Interaction) -> None:
    command = await start_developer_command(interaction, "off")
    if command is None:
        return
    await change_ai_settings(command, "switched off", lambda ai: setattr(ai, "disabled", True))
    await reply_in_chunks(command.ctx, "The AI assistant is **off**: it answers nobody, whatever their role. `/ai on` turns it back on.")


@ai_group.command(name="usage", description="Show the tokens used and what they cost, roughly")
@slash_check(check_is_developer)
async def ai_usage(interaction: discord.Interaction) -> None:
    command = await start_developer_command(interaction, "usage")
    if command is None:
        return
    report = command.ai.usage_report()
    await reply_in_chunks(
        command.ctx,
        f"**Last 24 hours**\n{usage_lines(report.last_24h)}\n\n**Since the bot started**\n{usage_lines(report.since_start)}\n\n"
        "The cost is an estimate from the tokens the API reported, at Anthropic's list prices. "
        "The counts start again from zero when the bot restarts; the Anthropic Console has the real bill.",
    )


async def autocomplete_model(interaction: discord.Interaction, partial: str) -> list[app_commands.Choice[str]]:
    """The models offered while a developer types one: those with a known price, or for a local model the
    ones installed on its server. Autocomplete runs no checks, so developers are checked here."""
    ai: AiAssistantService | None = interaction.client.services.ai  # type: ignore[attr-defined]
    models: list[str] = []
    if ai is not None and interaction_is_developer(interaction):
        models = await ai.installed_models() if ai.is_local else list(KNOWN_MODELS)
    return filter_choices(models, partial)


@ai_group.command(name="model", description="Change the model the assistant uses, or go back to the configured one")
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

    # The API (or the local server) has to know the model, or every answer would fail until somebody noticed.
    await ctx.defer(ephemeral=True)
    check = await ai.check_model(model)
    if isinstance(check, CheckOk) and not check.model_listed:
        if ai.is_local:
            installed = model_names(await ai.installed_models())
            await reply_in_chunks(
                ctx,
                f"The local model server doesn't have `{model}`, so nothing was changed. Install it there with `ollama pull {model}`. Installed: {installed}.",
            )
        else:
            await reply_in_chunks(
                ctx, f"The API doesn't list a model called `{model}`, so nothing was changed. Models with a known price: {model_names(KNOWN_MODELS)}."
            )
        return
    if isinstance(check, KeyRejected):
        await reply_in_chunks(ctx, f"The API refused the key ({check.reason}), so the model couldn't be checked and nothing was changed.")
        return
    if isinstance(check, Unreachable):
        server = "The local model server" if ai.is_local else "The API"
        await reply_in_chunks(ctx, f"{server} couldn't be reached to check the model ({check.problem}), so nothing was changed. Try again in a moment.")
        return

    await change_ai_settings(command, f"model set to `{model}`", lambda settings: setattr(settings, "model", model))
    price = price_per_million(model)
    if ai.is_local:
        price_note = "It runs locally, so it costs nothing per token."
    elif price is not None:
        price_note = f"Its list price is ${format_price(price[0])} per million tokens in and ${format_price(price[1])} out."
    else:
        price_note = "The bot doesn't know its price, so `/ai usage` can't estimate what it costs."
    await reply_in_chunks(ctx, f"The model is now `{model}`, from the next question on. {price_note}")


@ai_group.command(name="effort", description="Change how much effort the model puts into an answer. Lower is quicker and cheaper")
@slash_check(check_is_developer)
@app_commands.describe(level="The effort, or as configured")
@app_commands.choices(level=EFFORT_CHOICES)
async def ai_effort(interaction: discord.Interaction, level: str) -> None:
    command = await start_developer_command(interaction, "effort")
    if command is None:
        return
    ctx, ai = command.ctx, command.ai
    if ai.is_local:
        await reply_in_chunks(ctx, "A local model has no effort setting, so nothing was changed.")
        return

    stored = None if level == "configured" else level
    shown = "None" if stored is None else f'Some("{stored}")'
    await change_ai_settings(command, f"effort set to {shown}", lambda settings: setattr(settings, "effort", stored))

    if stored is None:
        message = f"The effort is back to the configured one: {describe_effort(ai.configured_effort)}."
    elif stored == "off":
        message = "No effort is sent now."
    else:
        message = f"The effort is now `{stored}`."
    await reply_in_chunks(
        ctx,
        f"{message} It applies from the next question on. If the API rejects it, answers fail until you choose another; `/ai status` shows the latest failure.",
    )


@ai_group.command(name="maintenance", description="Turn maintenance mode on or off: only Officers and Developers get answers")
@slash_check(check_is_developer)
@app_commands.describe(enabled="True to turn maintenance mode on, false to turn it off")
async def ai_maintenance(interaction: discord.Interaction, enabled: bool) -> None:
    command = await start_developer_command(interaction, "maintenance")
    if command is None:
        return
    settings = await change_ai_settings(command, "maintenance mode on" if enabled else "maintenance mode off", lambda ai: setattr(ai, "maintenance", enabled))

    if enabled and not settings.disabled:
        message = "Maintenance mode is **on**: the assistant answers only Officers and Developers."
    elif enabled:
        message = "Maintenance mode is **on**, but the assistant is off, so it answers nobody. `/ai on` turns it on."
    elif not settings.disabled:
        message = "Maintenance mode is **off**: the assistant answers all raiders again."
    else:
        message = "Maintenance mode is **off**, but the assistant is still off. `/ai on` turns it on."
    await reply_in_chunks(command.ctx, message)


@ai_group.command(name="limits", description="Show how many questions a member has asked in the last 10 minutes")
@slash_check(check_is_developer)
@app_commands.describe(user="The member to look up")
async def ai_limits(interaction: discord.Interaction, user: discord.User) -> None:
    command = await start_developer_command(interaction, "limits")
    if command is None:
        return
    usage = command.ai.member_usage(user.id)
    note = " They have to wait for the oldest to leave the window, or `/ai reset` gives the allowance back." if usage.asked >= usage.allowed else ""
    await reply_in_chunks(
        command.ctx, f"<@{user.id}> has asked {usage.asked} of {usage.allowed} questions in the last {int(usage.window_secs) // 60} minutes.{note}"
    )


@ai_group.command(name="reset", description="Give a member their whole allowance of questions back")
@slash_check(check_is_developer)
@app_commands.describe(user="The member whose limit to reset")
async def ai_reset(interaction: discord.Interaction, user: discord.User) -> None:
    command = await start_developer_command(interaction, "reset")
    if command is None:
        return
    ctx, ai = command.ctx, command.ai
    cleared = ai.reset_member(user.id)
    log.info(
        "audit: %s (%s) reset the AI limit of %s in guild %s, forgetting %d question(s)", ctx.author.id, ctx.author.name, user.id, command.guild_id, cleared
    )
    await reply_in_chunks(
        ctx, f"<@{user.id}>'s limit is reset: {cleared} counted question(s) forgotten, so they may ask {ai.member_usage(user.id).allowed} again."
    )


@ai_group.command(name="check", description="Check the registered roles, the API key and the model")
@slash_check(check_is_developer)
async def ai_check(interaction: discord.Interaction) -> None:
    command = await start_developer_command(interaction, "check")
    if command is None:
        return
    ctx, ai, guild_id = command.ctx, command.ai, command.guild_id
    await ctx.defer(ephemeral=True)

    settings = ctx.services.guild_settings.get(guild_id)
    model, _ = ai.resolve(settings.ai)
    findings: list[tuple[Finding, str]] = []

    # The registered roles have to exist in this server, or they grant nobody anything.
    existing = await ctx.services.guild_validation.existing_role_ids(guild_id)
    roles = [
        ("Raider role", settings.raider_role_id, True),
        ("Moderator (Officer) role", settings.mod_role_id, True),
        ("Developer role", settings.dev_role_id, True),
        ("Trial role", settings.trial_role_id, False),
    ]
    for label, role_id, needed in roles:
        if role_id is not None and role_id == guild_id:
            findings.append((Finding.BAD, f"{label}: `@everyone` is registered, which would be everybody"))
        elif role_id is not None and role_id in existing:
            findings.append((Finding.GOOD, f"{label}: <@&{role_id}> exists"))
        elif role_id is not None:
            findings.append((Finding.BAD, f"{label}: <@&{role_id}> is registered but no longer exists in this server"))
        elif needed:
            findings.append((Finding.WARNING, f"{label}: not registered"))
    if settings.dev_role_id is not None and settings.dev_role_id in (settings.mod_role_id, settings.raider_role_id, settings.trial_role_id):
        findings.append((Finding.BAD, "The developer role is also registered as another role, so its holders are not only developers"))

    local = ai.is_local
    check = await ai.check_model(model)
    if isinstance(check, CheckOk) and check.model_listed:
        if local:
            findings.append((Finding.GOOD, f"The local model server answers and has `{model}` installed; nothing leaves it"))
        else:
            findings.append((Finding.GOOD, f"The API key works and the API lists `{model}`"))
    elif isinstance(check, CheckOk):
        if local:
            findings.append(
                (
                    Finding.BAD,
                    f"The local model server answers, but doesn't have `{model}`: answers will fail. Install it there with `ollama pull {model}`, or change it with `/ai model`",
                )
            )
        else:
            findings.append((Finding.BAD, f"The API key works, but the API doesn't list `{model}`: answers will fail. Change it with `/ai model`"))
    elif isinstance(check, KeyRejected):
        findings.append((Finding.BAD, f"The API refused the key: {check.reason}"))
    elif local:
        findings.append((Finding.BAD, f"The local model server couldn't be reached: {check.problem}. Is the Ollama container up, and on the bot's network?"))
    else:
        findings.append((Finding.WARNING, f"The key couldn't be checked: {check.problem}"))

    if settings.ai.disabled:
        findings.append((Finding.WARNING, "The assistant is off (`/ai on`)"))
    if settings.ai.maintenance:
        findings.append((Finding.WARNING, "The assistant is in maintenance mode (`/ai maintenance`)"))
    answered, limit = ai.daily_usage()
    if answered >= limit:
        findings.append((Finding.BAD, f"The limit for the last 24 hours is reached: {answered} of {limit}"))
    else:
        findings.append((Finding.GOOD, f"Questions answered in the last 24 hours: {answered} of {limit}"))
    failure_total, failures = ai.failure_report()
    if failures:
        latest = failures[0]
        findings.append((Finding.WARNING, f"{failure_total} API failure(s) since the bot started; the latest <t:{latest.at_unix}:R>: {latest.detail}"))

    problems = sum(1 for finding, _ in findings if finding is not Finding.GOOD)
    lines = "\n".join(f"{finding.value} {text}" for finding, text in findings)
    summary = "**Nothing to fix.**" if problems == 0 else f"**{problems} thing(s) to look at.**"
    await reply_in_chunks(ctx, f"**AI assistant check**\n{lines}\n\n{summary}")


for _command in ai_group.commands:
    meta(_command, ephemeral=True)

COMMANDS = [ai_group]
