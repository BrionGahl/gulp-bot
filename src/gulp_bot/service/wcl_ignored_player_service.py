"""The players a guild's officers leave out of the Warcraft Logs overviews."""

from __future__ import annotations

from ..repository.wcl_ignored_player_repository import IgnoredPlayerRepository
from ..schema.wcl_settings_schema import NameListOutcome
from .name_list_service import NameListService

# The most players one server can ignore.
MAX_IGNORED = 50

# The longest name accepted: a character name is at most 12 letters, so this leaves room for a mistyped one
# without letting a whole sentence in.
MAX_NAME_CHARS = 24


class IgnoredPlayerService(NameListService):
    def __init__(self, repository: IgnoredPlayerRepository):
        super().__init__(repository, MAX_IGNORED)

    async def list_ignored(self, guild_id: int) -> list[str]:
        """Every player the guild ignores, in name order (ignoring case)."""
        return await self.names(guild_id)

    async def ignore(self, guild_id: int, name: str) -> NameListOutcome:
        return await self.add(guild_id, name)

    async def stop_ignoring(self, guild_id: int, name: str) -> bool:
        """Stops ignoring `name` (in any capitalisation). Returns whether the player was ignored."""
        return await self.remove(guild_id, name)
