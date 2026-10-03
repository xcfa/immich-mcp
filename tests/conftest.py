import json
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from fastmcp import Client

from immich_mcp.config import Settings
from immich_mcp.server import create_server

TOKEN = "test-mcp-token"
API_KEY = "immich-key"

ANNA = "11111111-1111-4111-8111-111111111111"
ANNA_SMITH = "22222222-2222-4222-8222-222222222222"
BOB = "33333333-3333-4333-8333-333333333333"


def asset(id: str, name: str = "IMG_0001.jpg", **extra: Any) -> dict[str, Any]:
    return {
        "id": id,
        "type": "IMAGE",
        "originalFileName": name,
        "localDateTime": "2023-07-14T18:22:05.000Z",
        "fileCreatedAt": "2023-07-14T15:22:05.000Z",
        "isFavorite": False,
        "duration": "0:00:00.00000",
        **extra,
    }


@dataclass
class FakeImmich:
    """In-process stand-in for the Immich REST API."""

    assets: list[dict[str, Any]] = field(default_factory=list)
    people: list[dict[str, Any]] = field(default_factory=list)
    tags: list[dict[str, Any]] = field(default_factory=list)
    albums: list[dict[str, Any]] = field(default_factory=list)
    places: dict[str, list[str]] = field(default_factory=dict)
    thumbnails: dict[str, bytes] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)
    next_page: str | None = None

    def last(self, path: str) -> httpx.Request:
        return [r for r in self.requests if r.url.path == path][-1]

    def body(self, path: str) -> dict[str, Any]:
        return json.loads(self.last(path).content)

    def called(self, path: str) -> bool:
        return any(r.url.path == path for r in self.requests)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("x-api-key") != API_KEY:
            return httpx.Response(401, json={"message": "Invalid API key", "statusCode": 401})
        return self.route(request, request.url.path.removeprefix("/api"))

    def route(self, request: httpx.Request, path: str) -> httpx.Response:
        q = request.url.params
        if path in ("/search/metadata", "/search/smart"):
            size = json.loads(request.content).get("size", 250)
            items = self.assets[:size]
            return httpx.Response(
                200,
                json={
                    "albums": {"total": 0, "count": 0, "items": [], "facets": []},
                    "assets": {
                        "total": len(items),
                        "count": len(items),
                        "items": items,
                        "facets": [],
                        "nextPage": self.next_page,
                    },
                },
            )
        if path == "/search/random":
            return httpx.Response(200, json=self.assets[: json.loads(request.content)["size"]])
        if path == "/search/person":
            name = q["name"].lower()
            return httpx.Response(
                200, json=[p for p in self.people if name in (p.get("name") or "").lower()]
            )
        if path == "/people":
            page, size = int(q["page"]), int(q["size"])
            chunk = self.people[(page - 1) * size : page * size]
            return httpx.Response(
                200,
                json={
                    "people": chunk,
                    "total": len(self.people),
                    "hidden": 0,
                    "hasNextPage": page * size < len(self.people),
                },
            )
        if path == "/tags":
            return httpx.Response(200, json=self.tags)
        if path == "/albums":
            return httpx.Response(200, json=self.albums)
        if path == "/search/suggestions":
            scope = q.get("state") or q.get("country") or ""
            return httpx.Response(200, json=self.places.get(f"{q['type']}:{scope}", []))
        if path.startswith("/assets/"):
            parts = path.strip("/").split("/")
            found = [a for a in self.assets if a["id"] == parts[1]]
            if not found:
                return httpx.Response(400, json={"message": "Not found or no asset.read access"})
            if len(parts) == 3 and parts[2] == "thumbnail":
                return httpx.Response(
                    200,
                    content=self.thumbnails.get(parts[1], b"\x89PNG..."),
                    headers={"content-type": "image/webp"},
                )
            return httpx.Response(200, json=found[0])
        return httpx.Response(404, json={"message": f"Cannot {request.method} {path}"})


@pytest.fixture
def fake() -> FakeImmich:
    return FakeImmich()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        immich_url="http://immich-server:2283/",
        immich_api_key=API_KEY,
        immich_public_url="https://photos.example.com/",
        mcp_auth_token=TOKEN,
    )


@pytest.fixture
def server(fake: FakeImmich, settings: Settings):
    return create_server(settings, transport=httpx.MockTransport(fake.handler))


@pytest.fixture
async def mcp_client(server):
    async with Client(server) as client:
        yield client
