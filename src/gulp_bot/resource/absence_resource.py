"""`/absence`: report, list and take back absences from raid nights. Raiders, trials and Officers (the moderator
role and server administrators); an Officer may do it for another member too.

The bot also reads absences out of messages in personal officer channels (`officer_channel_resource`); the
reply it posts there carries an Undo button, defined here, that the member or an Officer can press.
"""

from __future__ import annotations

import re
from typing import Any

import discord
from discord import app_commands

from ..core.discord_context import GulpContext, slash_context
from ..core.discord_helpers import NO_MENTIONS
from ..core.permission_checks import check_is_raider_or_administrator, interaction_is_admin_or_moderator, is_admin_or_moderator, slash_check
from ..core.schedule_parsing import now_in_new_york, parse_date
from ..exception.schedule_exception import ScheduleProblem
from ..mapper.absence_mapper import absence_text, confirmation, member_list_text
from ..schema.absence_schema import AbsenceKind, NewAbsence
from ..service.absence_service import span_problem
from .resource_support import meta, reply

absence = app_commands.Group(name="absence", description="Report, list and take back absences from raid nights", guild_only=True)
meta(
    absence,
    category="WoW Guild",
    help_text=(
        "Absences are listed for the officers in the officer digest of each week's raid notes thread. You can also just say it in your "
        'personal officer channel ("can\'t make it thursday"): the bot reads it and replies with an Undo button. Officers can add or remove '
        "an absence for anyone."
    ),
)


async def _target(ctx: GulpContext, guild_id: int, member: discord.Member | None) -> int | None:
    """Whose absence a command is about: the member named, if an Officer named one, else the invoker. Tells
    a non-Officer who named someone else that they can't, and returns `None`."""
    if member is None or member.id == ctx.author.id:
        return ctx.author.id
    if not is_admin_or_moderator(ctx, guild_id):
        await reply(ctx, "Only officers can manage someone else's absences.")
        return None
    return member.id


@absence.command(name="add", description="Report an absence from one or more raid nights")
@slash_check(check_is_raider_or_administrator)
@app_commands.describe(
    start="First day: tue, tomorrow, 11/4 or 2026-11-04",
    end="Last day, for more than one day (same format)",
    tentative="Late, or not sure yet, rather than out",
    note="Why, or anything officers should know (they can see it)",
    member="Officers: the member to add it for",
)
async def add(
    interaction: discord.Interaction,
    start: str,
    end: str | None = None,
    tentative: bool = False,
    note: str | None = None,
    member: discord.Member | None = None,
) -> None:
    ctx = await slash_context(interaction)
    guild_id = ctx.guild_id
    if guild_id is None:
        return
    user_id = await _target(ctx, guild_id, member)
    if user_id is None:
        return

    today = now_in_new_york().date()
    try:
        first = parse_date(start, today)
        last = first if end is None else parse_date(end, today)
    except ScheduleProblem as problem:
        await reply(ctx, str(problem))
        return
    problem = span_problem(first, last, today)
    if problem is not None:
        await reply(ctx, problem)
        return
    absences = ctx.services.absences
    nights = absences.nights_covered(guild_id, first, last)
    if nights is not None and not nights:
        await reply(ctx, "There's no raid night on those days, so there's nothing to miss.")
        return

    stored = await absences.add(NewAbsence(guild_id, user_id, first, last, AbsenceKind.TENTATIVE if tentative else AbsenceKind.ABSENT, note, ctx.author.id))
    whose = "" if user_id == ctx.author.id else f"<@{user_id}>: "
    text = confirmation(stored, nights) if nights is not None else f"Noted: {absence_text(stored)}. (Raid nights aren't set for this server yet.)"
    await reply(ctx, f"{whose}{text} Take it back with `/absence remove {stored.id}`.")


async def autocomplete_absence(interaction: discord.Interaction, partial: str) -> list[app_commands.Choice[int]]:
    """The invoker's own upcoming absences. Autocomplete runs no checks; an Officer removing someone else's
    absence types its number from `/absence list`."""
    guild_id = interaction.guild_id
    if guild_id is None:
        return []
    upcoming = await interaction.client.services.absences.upcoming_for_member(guild_id, interaction.user.id)  # type: ignore[attr-defined]
    choices = [app_commands.Choice(name=f"#{entry.id} {absence_text(entry)}"[:100], value=entry.id) for entry in upcoming]
    return [choice for choice in choices if partial.strip().lower() in choice.name.lower()][:25]


@absence.command(name="remove", description="Take back an absence")
@slash_check(check_is_raider_or_administrator)
@app_commands.describe(id="The absence, from the list or /absence list")
@app_commands.autocomplete(id=autocomplete_absence)
async def remove(interaction: discord.Interaction, id: int) -> None:
    ctx = await slash_context(interaction)
    guild_id = ctx.guild_id
    if guild_id is None:
        return
    absences = ctx.services.absences
    found = await absences.find(guild_id, id)
    if found is None or (found.user_id != ctx.author.id and not is_admin_or_moderator(ctx, guild_id)):
        await reply(ctx, f"You have no absence **#{id}**.")
        return
    await absences.remove(guild_id, id)
    await reply(ctx, f"Removed: {absence_text(found)}." if found.user_id == ctx.author.id else f"Removed <@{found.user_id}>'s {absence_text(found)}.")


@absence.command(name="list", description="List upcoming absences")
@slash_check(check_is_raider_or_administrator)
@app_commands.describe(member="Officers: the member whose absences to list")
async def list_absences(interaction: discord.Interaction, member: discord.Member | None = None) -> None:
    ctx = await slash_context(interaction)
    guild_id = ctx.guild_id
    if guild_id is None:
        return
    user_id = await _target(ctx, guild_id, member)
    if user_id is None:
        return
    upcoming = await ctx.services.absences.upcoming_for_member(guild_id, user_id)
    text = member_list_text(upcoming)
    await reply(ctx, text if user_id == ctx.author.id else f"<@{user_id}>:\n{text}".replace("You have", "They have"))


for _command in absence.commands:
    meta(_command, ephemeral=True)


class UndoAbsenceButton(discord.ui.DynamicItem[discord.ui.Button[Any]], template=r"absence:undo:(?P<id>\d+)"):
    """Under the bot's reply to an absence read out of a personal officer channel. Works after a restart:
    the absence's ID is in the button's custom ID."""

    def __init__(self, absence_id: int) -> None:
        super().__init__(discord.ui.Button(label="Undo", style=discord.ButtonStyle.secondary, custom_id=f"absence:undo:{absence_id}"))
        self.absence_id = absence_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button[Any], match: re.Match[str], /) -> UndoAbsenceButton:  # type: ignore[override]
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        guild_id = interaction.guild_id
        if guild_id is None:
            return
        absences = interaction.client.services.absences  # type: ignore[attr-defined]
        found = await absences.find(guild_id, self.absence_id)
        if found is None:
            await interaction.response.edit_message(content="Already removed.", view=None)
            return
        if interaction.user.id != found.user_id and not interaction_is_admin_or_moderator(interaction):
            await interaction.response.send_message(f"Only <@{found.user_id}> or an officer can undo this.", ephemeral=True, allowed_mentions=NO_MENTIONS)
            return
        await absences.remove(guild_id, self.absence_id)
        await interaction.response.edit_message(content=f"~~Noted: {absence_text(found)}.~~ Removed.", view=None, allowed_mentions=NO_MENTIONS)


def undo_view(absence_id: int) -> discord.ui.View:
    """The Undo button for a recorded absence, to post with the reply."""
    view = discord.ui.View(timeout=None)
    view.add_item(UndoAbsenceButton(absence_id))
    return view


COMMANDS = [absence]
