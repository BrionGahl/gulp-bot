"""The WowUtils API, which takes the guild's droptimizer reports."""

from __future__ import annotations

import logging

from ..core.http_client import HttpClient, RequestFailed, RequestTimeout, status_text
from ..exception.wowutils_exception import WowUtilsError
from ..schema.guild_settings_schema import WowUtilsCredentials

log = logging.getLogger(__name__)

API_URL = "https://api.wowutils.com/v1/"

# How long WowUtils may take to take a report.
REQUEST_TIMEOUT = 30.0


class WowUtilsClient:
    def __init__(self, http: HttpClient):
        self._http = http

    async def submit_droptimizer(self, credentials: WowUtilsCredentials, url: str, profile_key: str) -> None:
        """Submits a Raidbots droptimizer report URL and profile key to the WowUtils group."""
        group_id = credentials.group_id
        log.info("Submitting droptimizer report to WowUtils group %s", group_id)
        try:
            response = await self._http.request(
                "POST",
                f"{API_URL}groups/{group_id}/droptimizers",
                timeout=REQUEST_TIMEOUT,
                headers={"Authorization": f"Bearer {credentials.token}"},
                json={"url": url, "profileKey": profile_key},
            )
        except (RequestTimeout, RequestFailed) as e:
            raise WowUtilsError(f"WowUtils couldn't be reached: {e}") from None
        if not response.ok:
            raise WowUtilsError(f"WowUtils API returned {status_text(response.status)}: {response.body}")
        log.info("Successfully submitted droptimizer report to WowUtils group %s", group_id)
