"""`/ai check`: what may stop the AI features from working in a guild, each as a finding that is good, worth a
look, or broken. No Discord in here: the roles that exist and the model server's answer are handed in."""

from __future__ import annotations

from ..schema.ai_schema import ApiFailure, CheckOk, Finding, ModelCheck
from ..schema.guild_settings_schema import GuildSettings

Findings = list[tuple[Finding, str]]


def role_findings(settings: GuildSettings, guild_id: int, existing_role_ids: set[int]) -> Findings:
    """Whether the registered roles exist in the guild: a role that doesn't grants nobody anything, and
    `@everyone` would be everybody. Also whether the developer role is held only by developers."""
    findings: Findings = []
    roles = [
        ("Raider role", settings.raider_role_id, True),
        ("Moderator (Officer) role", settings.mod_role_id, True),
        ("Developer role", settings.dev_role_id, True),
        ("Trial role", settings.trial_role_id, False),
    ]
    for label, role_id, needed in roles:
        if role_id is not None and role_id == guild_id:
            findings.append((Finding.BAD, f"{label}: `@everyone` is registered, which would be everybody"))
        elif role_id is not None and role_id in existing_role_ids:
            findings.append((Finding.GOOD, f"{label}: <@&{role_id}> exists"))
        elif role_id is not None:
            findings.append((Finding.BAD, f"{label}: <@&{role_id}> is registered but no longer exists in this server"))
        elif needed:
            findings.append((Finding.WARNING, f"{label}: not registered"))
    if settings.dev_role_id is not None and settings.dev_role_id in (settings.mod_role_id, settings.raider_role_id, settings.trial_role_id):
        findings.append((Finding.BAD, "The developer role is also registered as another role, so its holders are not only developers"))
    return findings


def model_finding(model: str, check: ModelCheck) -> tuple[Finding, str]:
    """Whether the model server answers and has `model`."""
    if isinstance(check, CheckOk) and check.model_listed:
        return Finding.GOOD, f"The local model server answers and has `{model}` installed; nothing leaves it"
    if isinstance(check, CheckOk):
        return (
            Finding.BAD,
            f"The local model server answers, but doesn't have `{model}`: answers will fail. Install it there with `ollama pull {model}`, or change it with `/ai model`",
        )
    return Finding.BAD, f"The local model server couldn't be reached: {check.problem}. Is the Ollama container up, and on the bot's network?"


def state_findings(settings: GuildSettings, answered: int, limit: int, failure_total: int, failures: list[ApiFailure]) -> Findings:
    """The developers' switches, the limit on requests, and the latest failure."""
    findings: Findings = []
    if settings.ai.disabled:
        findings.append((Finding.WARNING, "The AI features are off (`/ai on`)"))
    if settings.ai.maintenance:
        findings.append((Finding.WARNING, "The AI features are in maintenance mode (`/ai maintenance`)"))
    if answered >= limit:
        findings.append((Finding.BAD, f"The limit for the last 24 hours is reached: {answered} of {limit}"))
    else:
        findings.append((Finding.GOOD, f"Requests answered in the last 24 hours: {answered} of {limit}"))
    if failures:
        latest = failures[0]
        findings.append((Finding.WARNING, f"{failure_total} failure(s) since the bot started; the latest <t:{latest.at_unix}:R>: {latest.detail}"))
    return findings
