import httpx
import pytest

from immich_mcp.client import ImmichClient, ImmichError

from .conftest import API_KEY, FakeImmich


def make_client(handler, api_key: str = API_KEY) -> ImmichClient:
    return ImmichClient(
        "http://immich-server:2283/", api_key, transport=httpx.MockTransport(handler)
    )


async def test_sends_api_key_under_api_prefix(fake: FakeImmich):
    client = make_client(fake.handler)

    assert await client.tags() == []

    req = fake.last("/api/tags")
    assert req.headers["x-api-key"] == API_KEY
    assert str(req.url) == "http://immich-server:2283/api/tags"


async def test_bad_api_key_is_reported_with_hint(fake: FakeImmich):
    client = make_client(fake.handler, api_key="wrong")

    with pytest.raises(ImmichError, match="Invalid API key.*IMMICH_API_KEY") as info:
        await client.tags()
    assert info.value.status == 401


async def test_validation_messages_are_joined():
    client = make_client(
        lambda r: httpx.Response(400, json={"message": ["size must be <= 1000", "bad page"]})
    )

    with pytest.raises(ImmichError, match="size must be <= 1000; bad page"):
        await client.search_metadata({})


async def test_unreachable_server_is_reported():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(ImmichError, match="unreachable.*connection refused"):
        await make_client(handler).albums()


async def test_suggestions_drop_empty_scope_and_values():
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, json=["Tokyo", None, ""])

    assert await make_client(handler).suggestions("city", country="Japan") == ["Tokyo"]
    assert dict(seen[0].params) == {"type": "city", "country": "Japan"}
