"""The commands as Discord gets them, registered on a real bot. Needs discord.py installed."""

import importlib.util
import inspect
import re
from pathlib import Path

from discord import app_commands

from gulp_bot.core import permission_checks
from gulp_bot.mapper.help_entry_mapper import help_entries
from gulp_bot.mapper.help_text_mapper import command_help_page, help_overview
from gulp_bot.resource.settings_resource import settings
from gulp_bot.resource.settings_wcl_resource import settings_wcl
from gulp_bot.service.help_service import find_command, with_paths

# Discord's limits on a message's content and on an embed description, where one command's page goes.
MAX_MESSAGE_CHARS = 2000
MAX_EMBED_DESCRIPTION_CHARS = 4096

TOP_LEVEL_ORDER = [
    "get_timeline_reminders",
    "class_discords",
    "submit_droptimizer",
    "source",
    "help",
    "register",
    "test_raid_notes",
    "test_raid_attendance",
    "test_replay",
    "settings",
    "announcement",
    "absence",
    "wcl",
    "ai",
    "tldr",
]


ROOT = Path(__file__).resolve().parent.parent


def load_generator():
    """`scripts/generate_commands_md.py`, which isn't a package: loaded from its file."""
    spec = importlib.util.spec_from_file_location("generate_commands_md", ROOT / "scripts" / "generate_commands_md.py")
    assert spec is not None and spec.loader is not None
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    return generator


GENERATOR = load_generator()
# The bot with every command registered, built the way COMMANDS.md is generated from.
BOT = GENERATOR.build_bot()


def entries():
    return help_entries(BOT.tree)


def all_app_commands(commands, depth=1):
    for command in commands:
        assert isinstance(command, (app_commands.Command, app_commands.Group))
        yield command, depth
        if isinstance(command, app_commands.Group):
            yield from all_app_commands(command.commands, depth + 1)


def test_every_command_is_registered_in_the_order_help_lists_them():
    assert [command.name for command in BOT.tree.get_commands()] == TOP_LEVEL_ORDER
    assert {command.name for command in BOT.commands} == set(TOP_LEVEL_ORDER[:10])


def test_every_expected_command_is_registered():
    names = {path for path, _ in with_paths(entries())}
    for expected in [
        "settings show",
        "settings mod_role",
        "settings dev_role",
        "settings bart_token",
        "settings raid_notes_schedule",
        "settings raid_nights",
        "settings raid_voice",
        "settings wcl client",
        "settings wcl source",
        "settings wcl consumable_list",
        "settings wcl enemy_add",
        "settings wcl enemy_remove",
        "settings wcl enemy_list",
        "settings clear",
        "announcement add",
        "wcl report",
        "wcl replay",
        "wcl damage_window",
        "ai status",
        "ai check",
        "tldr",
        "help",
        "register",
    ]:
        assert expected in names
    assert settings_wcl.app_command.qualified_name == "settings wcl"


def test_help_overview_fits_in_one_message():
    assert len(help_overview(entries())) <= MAX_MESSAGE_CHARS


def test_help_for_every_command_and_subcommand_fits_in_an_embed():
    for path, command in with_paths(entries()):
        if command.hidden:
            continue
        found = find_command(entries(), path)
        assert found is not None and found[0] == path
        assert len(command_help_page(path, found[1])) <= MAX_EMBED_DESCRIPTION_CHARS, path


def test_commands_fit_discords_registration_limits():
    for command, depth in all_app_commands(BOT.tree.get_commands()):
        assert depth <= 3, command.qualified_name
        assert re.fullmatch(r"[-_a-z0-9]{1,32}", command.name), command.name
        assert 1 <= len(command.description) <= 100, command.qualified_name
        if isinstance(command, app_commands.Group):
            assert len(command.commands) <= 25, command.qualified_name
        else:
            assert len(command.parameters) <= 25, command.qualified_name
            for parameter in command.parameters:
                assert len(parameter.description) <= 100, f"{command.qualified_name} {parameter.name}"


def test_commands_that_answer_privately_are_marked_so_on_both_halves():
    for name in ("source", "help", "settings", "get_timeline_reminders"):
        command = BOT.get_command(name)
        assert command is not None and command.extras["ephemeral"], name
        assert command.app_command.extras["ephemeral"], name  # type: ignore[union-attr]
    # A summary is for the whole channel.
    assert not BOT.tree.get_command("tldr").extras.get("ephemeral", False)  # type: ignore[union-attr]


def test_a_slash_only_command_is_not_a_prefix_one():
    # Tokens and secrets are only ever given through slash commands.
    prefix_names = {command.name for command in settings.commands}
    assert "bart_token" not in prefix_names and "wowutils" not in prefix_names
    assert "client" not in {command.name for command in settings_wcl.commands}


def test_every_service_is_built():
    services = BOT.services
    assert services.guild_settings is not None and services.warcraft_logs is not None and services.announcements is not None
    assert services.ai is not None


def test_commands_md_lists_the_commands_as_they_are():
    written = (ROOT / "COMMANDS.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert written == GENERATOR.render(BOT), "COMMANDS.md is out of date: run uv run scripts/generate_commands_md.py"


def test_commands_md_says_who_every_permission_check_lets_through():
    # A check missing from the generator's table would make its commands read "Everyone".
    checks = {name for name, _ in inspect.getmembers(permission_checks, inspect.iscoroutinefunction) if name.startswith("check_is_")}
    assert checks == set(GENERATOR.WHO_BY_CHECK)
