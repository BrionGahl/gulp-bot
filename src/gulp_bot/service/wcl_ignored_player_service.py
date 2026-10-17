"""The players a guild's officers leave out of the Warcraft Logs overviews."""

from __future__ import annotations

from ..repository.wcl_ignored_player_repository import IgnoredPlayerRepository
from ..schema.wcl_settings_schema import IgnorePlayerOutcome

# The most players one server can ignore.
MAX_IGNORED = 50

# The longest name accepted: a character name is at most 12 letters, so this leaves room for a mistyped one
# without letting a whole sentence in.
MAX_NAME_CHARS = 24


def same_name(a: str, b: str) -> bool:
    """Whether two character names are the same one: names compare ignoring case, including the accented
    letters character names can have."""
    return a.lower() == b.lower()


class IgnoredPlayerService:
    def __init__(self, repository: IgnoredPlayerRepository):
        self._repository = repository

    async def list_ignored(self, guild_id: int) -> list[str]:
        """Every player the guild ignores, in name order (ignoring case)."""
        return await self._repository.list_for_guild(guild_id)

    async def ignore(self, guild_id: int, name: str) -> IgnorePlayerOutcome:
        ignored = await self._repository.list_for_guild(guild_id)
        if any(same_name(existing, name) for existing in ignored):
            return IgnorePlayerOutcome.ALREADY_IGNORED
        if len(ignored) >= MAX_IGNORED:
            return IgnorePlayerOutcome.FULL
        await self._repository.insert(guild_id, name)
        return IgnorePlayerOutcome.ADDED

    async def stop_ignoring(self, guild_id: int, name: str) -> bool:
        """Stops ignoring `name` (in any capitalisation). Returns whether the player was ignored."""
        stored = next((existing for existing in await self._repository.list_for_guild(guild_id) if same_name(existing, name)), None)
        if stored is None:
            return False
        await self._repository.delete(guild_id, stored)
        return True
