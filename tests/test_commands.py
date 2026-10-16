"""The commands as Discord gets them. Needs discord.py installed."""

import re

import pytest

pytest.importorskip("discord")

from discord import app_commands  # noqa: E402

from gulp_bot.commands import ai, announcement, ask, mod_debug, settings, tldr, utilities, wcl, wow  # noqa: E402
from gulp_bot.commands.help_text import find_command, overview, render_command_help, with_paths  # noqa: E402

# Discord's limits on a message's content and on an embed description, where one command's page goes.
MAX_MESSAGE_CHARS = 2000
MAX_EMBED_DESCRIPTION_CHARS = 4096

TOP_LEVEL = [
    wow.get_timeline_reminders.app_command,
    wow.class_discords.app_command,
    wow.submit_droptimizer.app_command,
    utilities.source.app_command,
    utilities.help_command.app_command,
    utilities.register.app_command,
    mod_debug.test_raid_notes.app_command,
    mod_debug.test_raid_attendance.app_command,
    mod_debug.test_replay.app_command,
    settings.settings.app_command,
    announcement.announcement,
    wcl.wcl,
    ask.ask,
    ai.ai_group,
    tldr.tldr,
]


class Tree:
    def get_commands(self):
        return TOP_LEVEL


def entries():
    return utilities.help_entries(Tree())  # type: ignore[arg-type]


def all_app_commands(commands, depth=1):
    for command in commands:
        yield command, depth
        if isinstance(command, app_commands.Group):
            yield from all_app_commands(command.commands, depth + 1)


def test_the_commands_are_the_rust_bots():
    names = {path for path, _ in with_paths(entries())}
    for expected in [
        "settings show",
        "settings mod_role",
        "settings dev_role",
        "settings bart_token",
        "settings wcl client",
        "settings wcl source",
        "settings wcl consumable_list",
        "settings clear",
        "announcement add",
        "wcl report",
        "wcl replay",
        "ai status",
        "ai check",
        "ask",
        "tldr",
        "help",
        "register",
    ]:
        assert expected in names
    assert len(settings.settings.app_command.commands) == 20
    assert len(settings.settings_wcl.app_command.commands) == 11


def test_help_overview_fits_in_one_message():
    assert len(overview(entries())) <= MAX_MESSAGE_CHARS


def test_help_for_every_command_and_subcommand_fits_in_an_embed():
    for path, command in with_paths(entries()):
        if command.hidden:
            continue
        found = find_command(entries(), path)
        assert found is not None and found[0] == path
        assert len(render_command_help(path, found[1])) <= MAX_EMBED_DESCRIPTION_CHARS, path


def test_commands_fit_discords_registration_limits():
    for command, depth in all_app_commands(TOP_LEVEL):
        assert depth <= 3, command.qualified_name
        assert re.fullmatch(r"[-_a-z0-9]{1,32}", command.name), command.name
        assert 1 <= len(command.description) <= 100, command.qualified_name
        if isinstance(command, app_commands.Group):
            assert len(command.commands) <= 25, command.qualified_name
        else:
            assert len(command.parameters) <= 25, command.qualified_name
            for parameter in command.parameters:
                assert len(parameter.description) <= 100, f"{command.qualified_name} {parameter.name}"


def test_a_slash_only_command_is_not_a_prefix_one():
    # Tokens and secrets are only ever given through slash commands.
    prefix_names = {command.name for command in settings.settings.commands}
    assert "bart_token" not in prefix_names and "wowutils" not in prefix_names
    assert "client" not in {command.name for command in settings.settings_wcl.commands}
