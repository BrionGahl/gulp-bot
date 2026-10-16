"""Droptimizer reports, submitted to the guild's WowUtils group."""

from __future__ import annotations

from ..client.wowutils_client import WowUtilsClient
from ..schema.guild_settings_schema import WowUtilsCredentials


class DroptimizerService:
    def __init__(self, wowutils: WowUtilsClient):
        self._wowutils = wowutils

    async def submit(self, credentials: WowUtilsCredentials, report_url: str, profile_key: str) -> None:
        """Submits a Raidbots droptimizer report to the guild's WowUtils group. Reports only ever go to the
        group the guild registered. Raises `WowUtilsError` if WowUtils doesn't take it."""
        await self._wowutils.submit_droptimizer(credentials, report_url, profile_key)
