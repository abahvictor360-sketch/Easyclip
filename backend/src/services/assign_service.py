"""Client for the Assign (Postiz-based scheduler) public API."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

API_PREFIX = "/api/public/v1"


class AssignError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


class AssignService:
    def __init__(self, config):
        self.base_url = config.assign_base_url
        self.api_key = config.assign_api_key

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    def _require_configured(self) -> None:
        if not self.configured:
            raise AssignError("Assign is not configured on this server.", 503)

    def _url(self, path: str) -> str:
        return f"{self.base_url}{API_PREFIX}{path}"

    @staticmethod
    def _client() -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=15.0))

    def _check(self, response: httpx.Response, action: str) -> Any:
        if response.status_code == 401:
            raise AssignError("Assign rejected the API key.", 502)
        if response.status_code >= 400:
            logger.warning(
                "Assign %s failed: %s %s", action, response.status_code, response.text[:500]
            )
            raise AssignError(f"Assign could not {action} ({response.status_code}).", 502)
        try:
            return response.json()
        except ValueError as exc:
            raise AssignError(
                f"Assign returned an unexpected response while trying to {action}."
            ) from exc

    async def list_channels(self) -> List[Dict[str, Any]]:
        self._require_configured()
        try:
            async with self._client() as client:
                response = await client.get(
                    self._url("/integrations"), headers={"Authorization": self.api_key}
                )
        except httpx.HTTPError as exc:
            raise AssignError("Could not reach Assign.") from exc
        channels = self._check(response, "list channels")
        return [
            {
                "id": channel.get("id"),
                "name": channel.get("name"),
                "identifier": channel.get("identifier"),
                "picture": channel.get("picture"),
            }
            for channel in channels
            if not channel.get("disabled")
        ]

    async def send_clip_as_draft(
        self,
        *,
        file_path: Path,
        filename: str,
        caption: str,
        integration_ids: List[str],
        date: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Upload the clip to Assign and create one draft per chosen channel."""
        self._require_configured()
        when = (date or datetime.now(timezone.utc)).astimezone(timezone.utc)
        headers = {"Authorization": self.api_key}
        try:
            async with self._client() as client:
                with file_path.open("rb") as handle:
                    upload = await client.post(
                        self._url("/upload"),
                        headers=headers,
                        files={"file": (filename, handle, "video/mp4")},
                    )
                media = self._check(upload, "upload the clip")
                if not media.get("id") or not media.get("path"):
                    raise AssignError("Assign did not return the uploaded media.")

                body = {
                    "type": "draft",
                    "shortLink": False,
                    "date": when.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    "tags": [],
                    "posts": [
                        {
                            "integration": {"id": integration_id},
                            "value": [
                                {
                                    "content": caption,
                                    "image": [{"id": media["id"], "path": media["path"]}],
                                }
                            ],
                            "settings": {},
                        }
                        for integration_id in integration_ids
                    ],
                }
                created = await client.post(self._url("/posts"), headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise AssignError("Could not reach Assign.") from exc
        result = self._check(created, "create the draft")
        return {"drafts": len(integration_ids), "result": result}
