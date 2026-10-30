"""The abilities (mechanics) a guild's officers track in the Warcraft Logs damage taken overview."""

from __future__ import annotations

from ..core.text import same_name
from ..repository.wcl_tracked_ability_repository import TrackedAbilityRepository
from ..schema.wcl_settings_schema import MAX_TRACKED, TrackAbilityOutcome, TrackAbilityResult, TrackedAbility


class TrackedAbilityService:
    def __init__(self, repository: TrackedAbilityRepository):
        self._repository = repository

    async def list_tracked(self, guild_id: int) -> list[TrackedAbility]:
        """Every ability the guild tracks, by name."""
        return await self._repository.list_for_guild(guild_id)

    async def track(self, guild_id: int, ability_id: int, name: str) -> TrackAbilityResult:
        """Tracks `ability_id` under `name`, or renames it if it is already tracked. Names are unique per
        guild, ignoring case."""
        tracked = await self._repository.list_for_guild(guild_id)
        already_tracked = any(ability.ability_id == ability_id for ability in tracked)

        for other in tracked:
            if other.ability_id != ability_id and same_name(other.name, name):
                return TrackAbilityResult(TrackAbilityOutcome.NAME_TAKEN, other.ability_id)
        if not already_tracked and len(tracked) >= MAX_TRACKED:
            return TrackAbilityResult(TrackAbilityOutcome.FULL)

        await self._repository.save(guild_id, TrackedAbility(ability_id, name))
        return TrackAbilityResult(TrackAbilityOutcome.RENAMED if already_tracked else TrackAbilityOutcome.ADDED)

    async def untrack(self, guild_id: int, name: str) -> bool:
        """Stops tracking the ability with this name (ignoring case). Returns whether there was one."""
        return await self._repository.delete_by_name(guild_id, name)
