"""The `wcl_tracked_enemies` table: the enemies a guild tracks in `/wcl damage_window`."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .name_list_repository import NameListRepository

if TYPE_CHECKING:
    import asyncpg


class TrackedEnemyRepository(NameListRepository):
    def __init__(self, pool: asyncpg.Pool):
        super().__init__(pool, "wcl_tracked_enemies")
