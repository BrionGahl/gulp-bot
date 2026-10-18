"""Writes COMMANDS.md, the list of the bot's commands, from the commands themselves.

Run it after adding or changing a command, from the root of the repository, with the dev requirements
installed:

    python scripts/generate_commands_md.py

The commands, their descriptions, parameters and notes come from the registered command tree, the same
place `/help` reads them from, so the file can't say something the bot doesn't. Who may use a command comes
from the checks on it. The few things members do without a command ("Bot please", "bot get them") are
written here by hand, in `MESSAGE_TRIGGERS`, except the spontaneous reactions, which are listed from
`reactions_service.REACTIONS`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from discord import app_commands
from discord.ext import commands

from gulp_bot.bot import PREFIX, GulpBot
from gulp_bot.core.config import Config
from gulp_bot.resource.registry import register_all_resources
from gulp_bot.service.guild_settings_service import GuildSettingsService
from gulp_bot.service.reactions_service import reaction_rows

OUTPUT = Path(__file__).resolve().parent.parent / "COMMANDS.md"

# Who a check lets through, by the name of the check in `core/permission_checks.py`.
WHO_BY_CHECK = {
    "check_is_administrator": "Server administrators only",
    "check_is_developer": "Developer role only (`/settings dev_role`); not even administrators without it",
    "check_is_admin_or_moderator": "Administrators and the moderator role",
    "check_is_moderator": "Moderator role",
    "check_is_raider": "Raider, trial and moderator roles",
}
# The strictest check wins when a command has more than one.
CHECK_ORDER = list(WHO_BY_CHECK)

# Commands whose access is decided inside the command rather than by a check.
WHO_BY_COMMAND = {
    "ask": "Raider role, moderator role, administrators and the developer role (the AI assistant's access)",
    "tldr": "Raider role, moderator role, administrators and the developer role (the AI assistant's access)",
}
EVERYONE = "Everyone"

MESSAGE_TRIGGERS = f"""## Without a command

| Message | What happens | Who |
|---|---|---|
| A message with the words **"Bot please"** (any case, any punctuation between them) | The AI assistant answers it in the channel. If the message is a reply, the message replied to goes along with the question | The AI assistant's access, like `/ask` |
| A reply to one of the assistant's answers | Carries the conversation on | The AI assistant's access, like `/ask` |
| A reply to someone's message with the words **"bot get them"** | Deletes the message replied to and barks. The bot needs Manage Messages in the channel | Moderator role, administrators and the developer role |
{reaction_rows()}| `@bot <command>` | The same as `{PREFIX}<command>` | As the command |
"""


def build_bot() -> GulpBot:
    """The bot with every command registered, without connecting to Discord or to a database."""
    config = Config.from_env({"DISCORD_TOKEN": "unused", "DATABASE_URL": "postgres://unused"})
    bot = GulpBot(config, pool=None, guild_settings=GuildSettingsService(repository=None, cache={}))  # type: ignore[arg-type]
    register_all_resources(bot)
    return bot


def check_names(checks: list[Any]) -> set[str]:
    """The names of the `check_is_*` functions behind `checks`. A slash-only command's check wraps the
    function (`slash_check`), so it is looked for in the wrapper's closure too."""
    names: set[str] = set()
    for check in checks:
        candidates = [check, *(cell.cell_contents for cell in getattr(check, "__closure__", None) or ())]
        names.update(name for candidate in candidates if (name := getattr(candidate, "__name__", "")) in WHO_BY_CHECK)
    return names


def prefix_commands(bot: GulpBot) -> dict[str, commands.Command[Any, ..., Any]]:
    """Every command that can be used with the prefix, by its full name."""
    return {command.qualified_name: command for command in bot.walk_commands()}


def who_may_use(path: str, command: app_commands.Command[Any, ..., Any], prefix: commands.Command[Any, ..., Any] | None) -> str:
    if path in WHO_BY_COMMAND:
        return WHO_BY_COMMAND[path]
    names = check_names(command.checks)
    if prefix is not None:
        names |= check_names(prefix.checks)
    for name in CHECK_ORDER:
        if name in names:
            return WHO_BY_CHECK[name]
    return EVERYONE


def usage(path: str, command: app_commands.Command[Any, ..., Any]) -> str:
    parameters = " ".join(f"<{p.name}>" if p.required else f"[{p.name}]" for p in command.parameters)
    return f"/{path} {parameters}".rstrip()


def describe(path: str, command: app_commands.Command[Any, ..., Any], prefixes: dict[str, Any]) -> list[str]:
    prefix = prefixes.get(path)
    details = [f"**Who:** {who_may_use(path, command, prefix)}"]
    details.append(f"also `{PREFIX}{path}`" if prefix is not None else "slash only")
    if command.extras.get("hidden"):
        details.append("not listed in `/help`")
    if command.extras.get("ephemeral"):
        details.append("answers privately")

    lines = [f"- **`{usage(path, command)}`** — {command.description}", f"  - {' · '.join(details)}"]
    for parameter in command.parameters:
        optional = "" if parameter.required else " (optional)"
        lines.append(f"  - `{parameter.name}`{optional}: {parameter.description}")
    if command.extras.get("help_text"):
        lines.append(f"  - {command.extras['help_text']}")
    return lines


def walk(command: app_commands.Command[Any, ..., Any] | app_commands.Group, path: str, prefixes: dict[str, Any]) -> list[str]:
    """The lines for `command`, or for each command of a group, the group's note said once above them."""
    if not isinstance(command, app_commands.Group):
        return describe(path, command, prefixes)
    lines: list[str] = []
    if command.extras.get("help_text"):
        # A paragraph of its own: blank lines around it, or Markdown would join it to the list item above.
        lines += ["", f"*`/{path}`: {command.extras['help_text']}*", ""]
    for child in command.commands:
        lines += walk(child, f"{path} {child.name}", prefixes)  # type: ignore[arg-type]
    return lines


def render(bot: GulpBot) -> str:
    prefixes = prefix_commands(bot)
    by_category: dict[str, list[app_commands.Command[Any, ..., Any] | app_commands.Group]] = {}
    for command in bot.tree.get_commands():
        if isinstance(command, (app_commands.Command, app_commands.Group)):
            by_category.setdefault(command.extras.get("category") or "Other", []).append(command)

    out = [
        "# Commands",
        "",
        "<!-- Generated by scripts/generate_commands_md.py from the registered commands. Don't edit by hand: run the script. -->",
        "",
        f"Every command is a slash command; the ones marked *also `{PREFIX}…`* work with the `{PREFIX}` prefix too. "
        "`<name>` is a required parameter, `[name]` an optional one. Roles are the ones registered with `/settings`.",
        "",
        "If the slash commands don't show up in a server, register them there with `?register` (moderators).",
        "",
    ]
    out += [f"- [{category}](#{category.lower().replace(' ', '-')})" for category in by_category]
    out += ["- [Without a command](#without-a-command)", ""]
    for category, members in by_category.items():
        out += [f"## {category}", ""]
        for command in members:
            out += walk(command, command.name, prefixes)
        out.append("")
    out.append(MESSAGE_TRIGGERS)
    return without_double_blank_lines("\n".join(out))


def without_double_blank_lines(text: str) -> str:
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text


def main() -> None:
    OUTPUT.write_text(render(build_bot()), encoding="utf-8", newline="\n")
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
