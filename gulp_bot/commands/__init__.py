"""The bot's commands. `register_all` adds them in the order `/help` lists them."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..bot import GulpBot


def register_all(bot: GulpBot) -> None:
    from . import ai, announcement, ask, mod_debug, settings, tldr, utilities, wcl, wow

    # Prefix-and-slash commands: adding one adds its slash form to the command tree too.
    for command in (
        wow.get_timeline_reminders,
        wow.class_discords,
        wow.submit_droptimizer,
        utilities.source,
        utilities.help_command,
        utilities.register,
        mod_debug.test_raid_notes,
        mod_debug.test_raid_attendance,
        mod_debug.test_replay,
        settings.settings,
    ):
        bot.add_command(command)

    # Slash-only commands.
    for app_command in (announcement.announcement, wcl.wcl, ask.ask, ai.ai_group, tldr.tldr):
        bot.tree.add_command(app_command)
