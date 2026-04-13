"""Async HTTP client for the Deferno backend REST API."""

from __future__ import annotations

import os
from typing import Any

import httpx


class DefernoError(RuntimeError):
    """Raised when the Deferno backend returns an error response."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"{status_code}: {message}")
        self.status_code = status_code
        self.message = message


class DefernoClient:
    """Thin async wrapper around the Deferno backend API.

    Holds the bearer token in memory. Every request goes through ``_request``
    which raises :class:`DefernoError` on non-2xx responses so tools can
    translate them into readable MCP errors.
    """

    def __init__(self, base_url: str, token: str | None = None, timeout: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "DefernoClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    @property
    def token(self) -> str | None:
        return self._token

    @token.setter
    def token(self, value: str | None) -> None:
        self._token = value

    @property
    def base_url(self) -> str:
        return self._base_url

    async def _ensure_authed(self) -> None:
        if self._token:
            return
        raise DefernoError(
            401,
            "not authenticated — call the `start_auth` tool to begin the "
            "login flow, or run `defernowork-mcp auth` in your terminal",
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        authed: bool = True,
        json_body: Any | None = None,
    ) -> Any:
        headers = {"content-type": "application/json"}
        if authed:
            await self._ensure_authed()
            headers["authorization"] = f"Bearer {self._token}"

        response = await self._client.request(
            method,
            path,
            headers=headers,
            json=json_body,
        )
        if response.status_code == 204 or not response.content:
            if 200 <= response.status_code < 300:
                return None
            raise DefernoError(response.status_code, response.reason_phrase or "error")

        try:
            payload = response.json()
        except ValueError:
            payload = {"message": response.text}

        if not (200 <= response.status_code < 300):
            message = payload.get("message") if isinstance(payload, dict) else str(payload)
            raise DefernoError(response.status_code, message or response.reason_phrase or "error")
        return payload

    # ------------------------------------------------------------------ auth
    async def cli_init(self) -> dict[str, Any]:
        """Start a CLI authentication session.

        Returns ``{session_id, auth_url}`` from the backend.  The caller
        should show ``auth_url`` to the user, then pass ``session_id`` and
        the code the user sees in their browser to :meth:`cli_verify`.
        """
        return await self._request("POST", "/auth/cli/init", authed=False)

    async def cli_verify(self, session_id: str, code: str) -> dict[str, Any]:
        """Exchange a CLI auth code for a bearer token.

        Returns ``{token, user}`` and stores the token in ``self._token``.
        """
        result = await self._request(
            "POST",
            "/auth/cli/verify",
            authed=False,
            json_body={"session_id": session_id, "code": code},
        )
        self._token = result["token"]
        return result

    async def register(
        self, username: str, password: str, invite_code: str | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"username": username, "password": password}
        if invite_code is not None:
            body["invite_code"] = invite_code
        return await self._request("POST", "/auth/register", authed=False, json_body=body)

    async def login(self, username: str, password: str) -> dict[str, Any]:
        result = await self._request(
            "POST",
            "/auth/login",
            authed=False,
            json_body={"username": username, "password": password},
        )
        self._token = result["token"]
        return result

    async def logout(self) -> None:
        await self._request("POST", "/auth/logout")
        self._token = None

    async def whoami(self) -> dict[str, Any]:
        return await self._request("GET", "/auth/me")

    # ------------------------------------------------------------------ tasks
    async def list_tasks(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/tasks")

    async def get_task(self, task_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/tasks/{task_id}")

    async def create_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/tasks", json_body=payload)

    async def update_task(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("PATCH", f"/tasks/{task_id}", json_body=payload)

    async def split_task(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", f"/tasks/{task_id}/split", json_body=payload)

    async def merge_task(self, task_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/tasks/{task_id}/merge", json_body={})

    async def fold_task(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", f"/tasks/{task_id}/fold", json_body=payload)

    async def daily_tasks(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/tasks/today")

    async def mood_history(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/tasks/mood-history")
