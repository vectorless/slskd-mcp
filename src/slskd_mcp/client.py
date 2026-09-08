"""A small async client for the slskd HTTP API.

Deliberately hand-written rather than generated. slskd's OpenAPI spec has three
defects that break codegen: 14 paths carry a literal ``v{version}`` placeholder,
``components.securitySchemes`` is empty so auth is never wired up, and the
search endpoints declare no response schemas at all.

There is an existing ``slskd-api`` package on PyPI which is more complete than
this. It is AGPL-3.0, and depending on it would make this project AGPL; these
~100 lines are the price of staying MIT. If you don't care about that, use it
instead -- it covers rooms, shares, conversations and much more.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx2 as httpx

DEFAULT_URL = "http://localhost:5030"

# httpx logs a line per request at INFO. On a stdio MCP server that goes to the
# client's stderr log for every poll, which is noise rather than signal.
logging.getLogger("httpx2").setLevel(logging.WARNING)


class SlskdError(Exception):
    """Any failure talking to slskd, including non-2xx responses."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class Client:
    """Talks to one slskd instance.

    Usage::

        async with Client.from_env() as c:
            search = await c.start_search("Planet Rhythm")
    """

    def __init__(
        self, base_url: str = DEFAULT_URL, api_key: str = "", timeout: float = 30.0
    ) -> None:
        self.base = base_url.rstrip("/")
        self._http = httpx.AsyncClient(
            headers={"X-API-Key": api_key},
            timeout=timeout,
        )

    @classmethod
    def from_env(cls) -> "Client":
        """Reads ``SLSKD_URL`` (default localhost:5030) and ``SLSKD_API_KEY``."""
        key = os.environ.get("SLSKD_API_KEY")
        if not key:
            raise SlskdError("SLSKD_API_KEY is not set")
        return cls(os.environ.get("SLSKD_URL", DEFAULT_URL), key)

    async def __aenter__(self) -> "Client":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    def _url(self, path: str) -> str:
        """Always ``v0``. The spec's ``v{version}`` placeholder is a generation bug."""
        return f"{self.base}/api/v0/{path.lstrip('/')}"

    async def _send(
        self,
        method: str,
        path: str,
        body: Any = None,
        params: dict | None = None,
    ) -> Any:
        return (await self._send_full(method, path, body, params))[1]

    async def _send_full(
        self,
        method: str,
        path: str,
        body: Any = None,
        params: dict | None = None,
    ) -> tuple[int, Any]:
        """Like `_send`, but also hands back the status code.

        The batch enqueue endpoint distinguishes total failure (200) from full
        success (201) and partial success (207), so some callers need it.
        """
        try:
            r = await self._http.request(
                method, self._url(path), json=body, params=params
            )
        except httpx.HTTPError as e:
            raise SlskdError(f"could not reach slskd at {self.base}: {e}") from e

        text = r.text
        if r.status_code >= 400:
            raise SlskdError(
                f"slskd returned {r.status_code}: {text[:400]}", status=r.status_code
            )
        # Several endpoints answer 204 with an empty body.
        if not text.strip():
            return r.status_code, None
        try:
            return r.status_code, json.loads(text)
        except ValueError as e:
            raise SlskdError(f"could not decode response: {e}", r.status_code) from e

    async def health(self) -> bool:
        """Cheap liveness check. Unauthenticated, and outside /api/v0."""
        try:
            r = await self._http.get(f"{self.base}/health")
        except httpx.HTTPError:
            return False
        return r.status_code < 400

    # ---- searches ----

    async def start_search(self, text: str) -> dict:
        return await self._send("POST", "searches", {"searchText": text})

    async def searches(self) -> list[dict]:
        return await self._send("GET", "searches") or []

    async def search(self, id: str) -> dict:
        return await self._send("GET", f"searches/{id}")

    async def search_responses(self, id: str) -> list[dict]:
        return await self._send("GET", f"searches/{id}/responses") or []

    async def stop_search(self, id: str) -> None:
        await self._send("PUT", f"searches/{id}")

    # ---- users ----

    async def browse(self, username: str) -> Any:
        return await self._send("GET", f"users/{username}/browse")

    async def user_info(self, username: str) -> Any:
        return await self._send("GET", f"users/{username}/info")

    # ---- transfers ----

    async def downloads(self) -> Any:
        return await self._send("GET", "transfers/downloads")

    async def downloads_for(self, username: str) -> Any:
        return await self._send("GET", f"transfers/downloads/{username}")

    async def download_status(self, username: str, id: str) -> dict:
        """One transfer, including `placeInQueue`."""
        return await self._send("GET", f"transfers/downloads/{username}/{id}")

    async def enqueue(
        self,
        username: str,
        files: list[dict],
        search_id: str | None = None,
        destination: str | None = None,
    ) -> tuple[int, Any]:
        """Queue files from one user. The only method that creates network activity.

        Uses the batch endpoint. `POST transfers/downloads/{username}` is marked
        deprecated in the spec, and the batch form additionally carries a
        `searchId` linking a download back to the search that found it.

        Returns `(status, body)` because the status is load-bearing here and
        counter-intuitive: **200 means every file failed**, 201 means all
        succeeded, 207 means some of each. The body's `failures` list says why.
        """
        body: dict[str, Any] = {"username": username, "files": files}
        if search_id:
            body["searchId"] = search_id
        if destination:
            body["options"] = {"destination": destination}
        return await self._send_full("POST", "transfers/downloads/batches", body)

    async def cancel_download(
        self, username: str, id: str, remove: bool = False
    ) -> None:
        """Cancel a transfer in flight. `remove` also drops it from the list."""
        await self._send(
            "DELETE",
            f"transfers/downloads/{username}/{id}",
            params={"remove": str(bool(remove)).lower()},
        )

    async def clear_completed_downloads(self) -> None:
        await self._send("DELETE", "transfers/downloads/all/completed")
