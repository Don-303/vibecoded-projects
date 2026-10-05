"""Client for the Jobs service on the Hermes machine (port 8643).

The service reads job emails from Gmail and labels each one; Terminus only
reads its results. ``POST /jobs/refresh`` checks Gmail first (seconds, up to a
few minutes); ``GET /jobs`` returns the latest results instantly, with the time
they were made in ``X-Updated-At``. Both return one object per email:
``{id, from, subject, date, company, role, stage, summary, next_step, due}``.
"""

from __future__ import annotations

from datetime import datetime

import httpx

from terminus.agent_client import normalize_host

DEFAULT_PORT = 8643
REFRESH_TIMEOUT = 600  # seconds: a refresh after many new emails can take minutes


class JobsServiceError(Exception):
    """Something the user should see on the Jobs page."""


def service_url(configured: str | None, hermes_host: str) -> str:
    """The configured URL, else Hermes' host on port 8643."""
    if configured:
        return configured.rstrip("/")
    base = normalize_host(hermes_host)  # http://host:8642
    scheme, _, rest = base.partition("://")
    host = rest.split("/")[0].rsplit(":", 1)[0]
    return f"{scheme}://{host}:{DEFAULT_PORT}"


def parse_updated(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
    except ValueError:
        return None


class JobsService:
    def __init__(self, base_url: str, api_key: str | None, *,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.transport = transport

    async def _call(self, method: str, path: str, timeout: float) -> tuple[list[dict], datetime | None]:
        if not self.api_key:
            raise JobsServiceError("No API key configured (Terminus uses the Hermes key).")
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            async with httpx.AsyncClient(base_url=self.base_url, headers=headers,
                                         timeout=httpx.Timeout(timeout, connect=5),
                                         transport=self.transport) as client:
                resp = await client.request(method, path)
        except httpx.TimeoutException as exc:
            raise JobsServiceError(f"the Jobs service didn't answer in time ({path})") from exc
        except httpx.HTTPError as exc:
            raise JobsServiceError(f"can't reach the Jobs service at {self.base_url}") from exc
        if resp.status_code == 401:
            raise JobsServiceError("the Jobs service rejected the API key")
        if resp.status_code >= 400:
            raise JobsServiceError(f"Jobs service error {resp.status_code}: {resp.text[:120]}")
        data = resp.json()
        if not isinstance(data, list):
            raise JobsServiceError("the Jobs service returned something other than a list")
        return [e for e in data if isinstance(e, dict)], parse_updated(resp.headers.get("X-Updated-At"))

    async def refresh(self) -> tuple[list[dict], datetime | None]:
        """Check Gmail for new job emails, then return all results."""
        return await self._call("POST", "/jobs/refresh", REFRESH_TIMEOUT)

    async def latest(self) -> tuple[list[dict], datetime | None]:
        """The results of the last refresh (instant)."""
        return await self._call("GET", "/jobs", 30)
