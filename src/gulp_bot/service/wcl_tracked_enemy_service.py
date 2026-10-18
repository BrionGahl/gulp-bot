"""The enemies a guild's officers track in `/wcl damage_window`: who damaged them, and how much, in a moment
of a pull. Enemies are tracked by name, and every enemy of a report with that name counts as one."""

from __future__ import annotations

from ..repository.wcl_tracked_enemy_repository import TrackedEnemyRepository
from ..schema.wcl_settings_schema import TrackEnemyOutcome

# The most enemies one server can track. Each is a field of the embed, which has a size limit of its own.
MAX_TRACKED = 6

# The longest name accepted: it is a field title in the embed, and Discord allows 256 characters there.
MAX_NAME_CHARS = 100


def same_enemy(a: str, b: str) -> bool:
    """Whether two enemy names are the same one: names compare ignoring case, accented letters included."""
    return a.strip().lower() == b.strip().lower()


class TrackedEnemyService:
    def __init__(self, repository: TrackedEnemyRepository):
        self._repository = repository

    async def list_tracked(self, guild_id: int) -> list[str]:
        """Every enemy the guild tracks, in name order (ignoring case)."""
        return await self._repository.list_for_guild(guild_id)

    async def track(self, guild_id: int, name: str) -> TrackEnemyOutcome:
        tracked = await self._repository.list_for_guild(guild_id)
        if any(same_enemy(existing, name) for existing in tracked):
            return TrackEnemyOutcome.ALREADY_TRACKED
        if len(tracked) >= MAX_TRACKED:
            return TrackEnemyOutcome.FULL
        await self._repository.insert(guild_id, name)
        return TrackEnemyOutcome.ADDED

    async def untrack(self, guild_id: int, name: str) -> bool:
        """Stops tracking `name` (in any capitalisation). Returns whether it was tracked."""
        stored = next((existing for existing in await self._repository.list_for_guild(guild_id) if same_enemy(existing, name)), None)
        if stored is None:
            return False
        await self._repository.delete(guild_id, stored)
        return True
