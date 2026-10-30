"""A guild's list of names, with a limit on how long it may get: names compare ignoring case (`same_name`), so
a name is on the list once, whatever capitalisation it was added in."""

from __future__ import annotations

from ..core.text import same_name
from ..repository.name_list_repository import NameListRepository
from ..schema.wcl_settings_schema import NameListOutcome


class NameListService:
    def __init__(self, repository: NameListRepository, max_names: int):
        self._repository = repository
        self.max_names = max_names

    async def names(self, guild_id: int) -> list[str]:
        """Every name on the guild's list, in name order (ignoring case)."""
        return await self._repository.list_for_guild(guild_id)

    async def add(self, guild_id: int, name: str) -> NameListOutcome:
        listed = await self._repository.list_for_guild(guild_id)
        if any(same_name(existing, name) for existing in listed):
            return NameListOutcome.ALREADY_LISTED
        if len(listed) >= self.max_names:
            return NameListOutcome.FULL
        await self._repository.insert(guild_id, name)
        return NameListOutcome.ADDED

    async def remove(self, guild_id: int, name: str) -> bool:
        """Takes `name` (in any capitalisation) off the list. Returns whether it was on it."""
        stored = next((existing for existing in await self._repository.list_for_guild(guild_id) if same_name(existing, name)), None)
        if stored is None:
            return False
        await self._repository.delete(guild_id, stored)
        return True
