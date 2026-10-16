"""The commands as Discord gets them, registered on a real bot. Needs discord.py installed."""

import re

import pytest

pytest.importorskip("discord")

from discord import app_commands  # noqa: E402

from gulp_bot.bot import GulpBot  # noqa: E402
from gulp_bot.core.config import Config  # noqa: E402
from gulp_bot.mapper.help_entry_mapper import help_entries  # noqa: E402
from gulp_bot.mapper.help_text_mapper import command_help_page, help_overview  # noqa: E402
from gulp_bot.resource.registry import register_all_resources  # noqa: E402
from gulp_bot.resource.settings_resource import settings  # noqa: E402
from gulp_bot.resource.settings_wcl_resource import settings_wcl  # noqa: E402
from gulp_bot.service.guild_settings_service import GuildSettingsService  # noqa: E402
from gulp_bot.service.help_service import find_command, with_paths  # noqa: E402

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
    "wcl",
    "ask",
    "ai",
    "tldr",
]


def make_bot(**env):
    config = Config.from_env({"DISCORD_TOKEN": "token", "DATABASE_URL": "postgres://unused", **env})
    bot = GulpBot(config, pool=None, guild_settings=GuildSettingsService(repository=None, cache={}))  # type: ignore[arg-type]
    register_all_resources(bot)
    return bot


BOT = make_bot()


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
    assert len(settings.app_command.commands) == 20
    assert len(settings_wcl.app_command.commands) == 11
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
    assert not BOT.tree.get_command("ask").extras.get("ephemeral", False)  # type: ignore[union-attr]


def test_a_slash_only_command_is_not_a_prefix_one():
    # Tokens and secrets are only ever given through slash commands.
    prefix_names = {command.name for command in settings.commands}
    assert "bart_token" not in prefix_names and "wowutils" not in prefix_names
    assert "client" not in {command.name for command in settings_wcl.commands}


def test_every_service_is_built_and_the_assistant_only_with_a_backend():
    services = BOT.services
    assert services.guild_settings is not None and services.warcraft_logs is not None and services.announcements is not None
    assert services.ai is None
    assert make_bot(AI_BACKEND="local").services.ai is not None
    assert make_bot(ANTHROPIC_API_KEY="key").services.ai is not None
