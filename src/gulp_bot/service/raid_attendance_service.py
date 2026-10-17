"""Raid attendance: who is in the raid voice channel at the start and the end of each raid, posted to that
week's raid notes thread."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from ..core.embed import to_discord_embed
from ..mapper.raid_attendance_embed_mapper import snapshot_embed, summary_embed
from ..repository.raid_attendance_repository import RaidAttendanceRepository
from ..schema.raid_attendance_schema import AttendanceComparison, AttendanceMoment, StartSnapshot
from .raid_notes_service import RaidNotesService

if TYPE_CHECKING:
    import discord

log = logging.getLogger(__name__)


def compare_attendance(present_at_start: list[int], present_at_end: list[int]) -> AttendanceComparison:
    """Who stayed for the whole raid, who left before the end, and who joined after the start."""
    left_early = [user for user in present_at_start if user not in present_at_end]
    joined_late = [user for user in present_at_end if user not in present_at_start]
    return AttendanceComparison(len(present_at_start) - len(left_early), left_early, joined_late)


class RaidAttendanceService:
    def __init__(self, repository: RaidAttendanceRepository, raid_notes: RaidNotesService, discord_client: discord.Client):
        self._repository = repository
        self._raid_notes = raid_notes
        self._discord = discord_client

    async def record_snapshot(self, guild_id: int, voice_channel_id: int, raid_notes_channel_id: int, moment: AttendanceMoment, at: datetime) -> int:
        """Takes a snapshot of who is in `voice_channel_id` right now and posts it to the raid notes thread
        for the week containing `at`, creating that thread if the week doesn't have one yet (raids usually
        happen before the weekly thread's own schedule fires). At the end of a raid, also posts how it
        compares with the start. Returns the thread's ID."""
        # Taken before any awaiting below, so the list reflects the scheduled moment as closely as possible
        # rather than whenever Discord got round to creating the thread.
        present = self.members_in_voice(guild_id, voice_channel_id)

        week = await self._raid_notes.week_thread(guild_id, raid_notes_channel_id, at.date())
        thread = self._raid_notes.thread_messageable(guild_id, week.thread_id)

        # Mentions inside an embed never notify anyone, so the list is a record, not a summons.
        await thread.send(embed=to_discord_embed(snapshot_embed(moment, at, voice_channel_id, present)))
        log.info("Recorded raid %s attendance for guild %s: %d present", moment.value, guild_id, len(present))

        if moment is AttendanceMoment.START:
            await self._repository.save_start_snapshot(guild_id, voice_channel_id, at, present)
        elif moment is AttendanceMoment.END:
            start = await self._start_of_raid_ending(guild_id, voice_channel_id, at)
            comparison = None if start is None else compare_attendance(start.present, present)
            await thread.send(embed=to_discord_embed(summary_embed(start, at, comparison)))
        # A manual check is neither end of a raid: it mustn't disturb a real start snapshot.

        return week.thread_id

    async def _start_of_raid_ending(self, guild_id: int, voice_channel_id: int, end: datetime) -> StartSnapshot | None:
        """The stored start snapshot, if it is the start of this raid. Taken (not just read) so one start is
        only ever compared against one end."""
        start = await self._repository.take_start_snapshot(guild_id)
        if start is not None and not start.pairs_with(voice_channel_id, end):
            return None
        return start

    def members_in_voice(self, guild_id: int, voice_channel_id: int) -> list[int]:
        """Everyone currently connected to `voice_channel_id`, from the gateway cache, sorted by ID so the
        start and end lists are easy to compare. Bots are left out where the cache knows the account is one."""
        guild = self._discord.get_guild(guild_id)
        if guild is None:
            raise RuntimeError("the server isn't in the bot's cache, so its voice channels can't be read")

        present = []
        for user_id, voice_state in guild._voice_states.items():
            if voice_state.channel is None or voice_state.channel.id != voice_channel_id:
                continue
            member = guild.get_member(user_id)
            if member is not None and member.bot:
                continue
            present.append(user_id)
        return sorted(present)
