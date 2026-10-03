"""Thin async client for the Immich REST API (`/api/*`), authenticated with an API key."""

from typing import Any

import httpx

API_KEY_HEADER = "x-api-key"

Body = dict[str, Any]


class ImmichError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class ImmichClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._http = httpx.AsyncClient(
            base_url=f"{base_url.rstrip('/')}/api",
            timeout=timeout,
            transport=transport,
            headers={API_KEY_HEADER: api_key},
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> httpx.Response:
        try:
            resp = await self._http.request(method, path, params=params, json=json)
        except httpx.TransportError as exc:
            raise ImmichError(
                f"Immich is unreachable at {self._http.base_url}: {exc or type(exc).__name__}"
            ) from exc
        if resp.status_code >= 400:
            hint = ""
            if resp.status_code in (401, 403):
                hint = " Check IMMICH_API_KEY and its permissions."
            raise ImmichError(
                f"{method} {path} failed (HTTP {resp.status_code}): {_error_text(resp)}.{hint}",
                resp.status_code,
            )
        return resp

    # --- search -----------------------------------------------------------

    async def search_metadata(self, body: Body) -> Body:
        resp = await self.request("POST", "/search/metadata", json=body)
        return resp.json()["assets"]

    async def search_smart(self, body: Body) -> Body:
        resp = await self.request("POST", "/search/smart", json=body)
        return resp.json()["assets"]

    async def search_random(self, body: Body) -> list[Body]:
        resp = await self.request("POST", "/search/random", json=body)
        return resp.json()

    async def suggestions(
        self, kind: str, *, country: str | None = None, state: str | None = None
    ) -> list[str]:
        params = {"type": kind, "country": country, "state": state}
        resp = await self.request(
            "GET", "/search/suggestions", params={k: v for k, v in params.items() if v}
        )
        return [s for s in resp.json() if s]

    # --- library ----------------------------------------------------------

    async def search_people(self, name: str) -> list[Body]:
        resp = await self.request("GET", "/search/person", params={"name": name})
        return resp.json()

    async def people(self, *, page: int = 1, size: int = 200) -> Body:
        resp = await self.request("GET", "/people", params={"page": page, "size": size})
        return resp.json()

    async def tags(self) -> list[Body]:
        resp = await self.request("GET", "/tags")
        return resp.json()

    async def albums(self) -> list[Body]:
        # Without filters Immich returns both owned albums and albums shared with the user.
        resp = await self.request("GET", "/albums")
        return resp.json()

    async def asset(self, asset_id: str) -> Body:
        resp = await self.request("GET", f"/assets/{asset_id}")
        return resp.json()

    async def thumbnail(self, asset_id: str, size: str = "thumbnail") -> tuple[bytes, str]:
        resp = await self.request("GET", f"/assets/{asset_id}/thumbnail", params={"size": size})
        return resp.content, resp.headers.get("content-type", "image/jpeg")


def _error_text(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:300] or resp.reason_phrase
    if isinstance(data, dict):
        message = data.get("message") or data.get("error") or data
        if isinstance(message, list):
            message = "; ".join(map(str, message))
        return str(message)[:300]
    return str(data)[:300]
