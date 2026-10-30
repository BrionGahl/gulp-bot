"""The `wcl_ignored_players` table: the players a guild leaves out of the Warcraft Logs overviews."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .name_list_repository import NameListRepository

if TYPE_CHECKING:
    import asyncpg


class IgnoredPlayerRepository(NameListRepository):
    def __init__(self, pool: asyncpg.Pool):
        super().__init__(pool, "wcl_ignored_players")
