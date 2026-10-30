"""The enemies a guild's officers track in `/wcl damage_window`: who damaged them, and how much, in a moment
of a pull. Enemies are tracked by name, and every enemy of a report with that name counts as one."""

from __future__ import annotations

from ..repository.wcl_tracked_enemy_repository import TrackedEnemyRepository
from ..schema.wcl_settings_schema import MAX_TRACKED, NameListOutcome
from .name_list_service import NameListService


class TrackedEnemyService(NameListService):
    def __init__(self, repository: TrackedEnemyRepository):
        super().__init__(repository, MAX_TRACKED)

    async def list_tracked(self, guild_id: int) -> list[str]:
        """Every enemy the guild tracks, in name order (ignoring case)."""
        return await self.names(guild_id)

    async def track(self, guild_id: int, name: str) -> NameListOutcome:
        return await self.add(guild_id, name)

    async def untrack(self, guild_id: int, name: str) -> bool:
        """Stops tracking `name` (in any capitalisation). Returns whether it was tracked."""
        return await self.remove(guild_id, name)
