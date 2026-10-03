import pytest
from fastmcp.exceptions import ToolError
from mcp.types import ImageContent, TextContent

from immich_mcp.server import _date_bound

from .conftest import ANNA, ANNA_SMITH, BOB, FakeImmich, asset

PHOTO_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
VIDEO_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


@pytest.fixture
def library(fake: FakeImmich) -> FakeImmich:
    fake.people = [
        {"id": ANNA, "name": "Anna", "isHidden": False},
        {"id": ANNA_SMITH, "name": "Anna Smith", "isHidden": False},
        {"id": BOB, "name": "Bob", "isHidden": False},
        {"id": "unnamed", "name": "", "isHidden": False},
    ]
    fake.tags = [
        {"id": "t1", "name": "Travel", "value": "Travel"},
        {"id": "t2", "name": "Japan", "value": "Travel/Japan"},
        {"id": "t3", "name": "2023", "value": "Events/2023"},
        {"id": "t4", "name": "2023", "value": "Travel/2023"},
    ]
    fake.albums = [
        {"id": "al1", "albumName": "Summer in Kyoto", "assetCount": 120, "shared": True,
         "startDate": "2023-07-01T08:00:00.000Z", "endDate": "2023-07-20T21:00:00.000Z"},
        {"id": "al2", "albumName": "Summer 2024", "assetCount": 40},
    ]
    fake.places = {
        "country:": ["Japan", "Germany"],
        "state:Japan": ["Kyoto", "Tokyo"],
        "city:Japan": ["Kyoto", "Tokyo", "Uji"],
        "city:Kyoto": ["Kyoto", "Uji"],
        "city:": ["Kyoto", "Tokyo", "Uji", "München", "Berlin"],
    }
    return fake


async def test_lists_expected_tools(mcp_client):
    names = {t.name for t in await mcp_client.list_tools()}
    assert names == {
        "search_photos",
        "view_photos",
        "list_people",
        "list_tags",
        "list_albums",
        "list_locations",
    }


async def test_search_defaults_to_newest_metadata_search(mcp_client, fake: FakeImmich):
    fake.next_page = "3"

    page = (await mcp_client.call_tool("search_photos", {"limit": 10, "page": 2})).data

    assert fake.body("/api/search/metadata") == {
        "withExif": True,
        "withPeople": True,
        "order": "desc",
        "page": 2,
        "size": 10,
    }
    assert page.page == 2 and page.next_page == 3


async def test_search_combines_resolved_filters(mcp_client, library: FakeImmich):
    page = (
        await mcp_client.call_tool(
            "search_photos",
            {
                "people": ["anna", BOB],
                "tags": ["japan"],
                "album": "kyoto",
                "country": "japan",
                "state": "kyoto",
                "city": "uji",
                "taken_after": "2023",
                "taken_before": "2023-07",
                "media_type": "video",
                "favorites_only": True,
                "sort": "oldest",
            },
        )
    ).data

    body = library.body("/api/search/metadata")
    assert body["personIds"] == [ANNA, BOB]
    assert body["tagIds"] == ["t2"]
    assert body["albumIds"] == ["al1"]
    assert (body["country"], body["state"], body["city"]) == ("Japan", "Kyoto", "Uji")
    assert body["takenAfter"] == "2023-01-01T00:00:00.000Z"
    assert body["takenBefore"] == "2023-07-31T23:59:59.999Z"
    assert body["type"] == "VIDEO" and body["isFavorite"] is True and body["order"] == "asc"
    assert page.resolved == {
        "people": ["Anna"],
        "tags": ["Travel/Japan"],
        "album": ["Summer in Kyoto"],
        "country": ["Japan"],
        "state": ["Kyoto"],
        "city": ["Uji"],
    }


async def test_search_maps_photo_fields(mcp_client, fake: FakeImmich):
    fake.assets = [
        asset(
            PHOTO_ID,
            "beach.jpg",
            isFavorite=True,
            exifInfo={"city": "Kyoto", "state": "Kyoto", "country": "Japan",
                      "make": "FUJIFILM", "model": "X-T5", "description": "Sunset"},
            people=[{"id": ANNA, "name": "Anna"}, {"id": "x", "name": ""}],
            tags=[{"id": "t2", "value": "Travel/Japan"}],
        ),
        asset(
            VIDEO_ID, "clip.mp4", type="VIDEO", duration="0:01:05.500000",
            exifInfo={"make": "Canon", "model": "Canon EOS R5"},
        ),
        asset("cccccccc-cccc-4ccc-8ccc-cccccccccccc", "new.mp4", type="VIDEO", duration=12400),
    ]

    page = (await mcp_client.call_tool("search_photos", {})).data

    photo, video, new_video = page.photos
    assert page.count == 3 and page.next_page is None
    assert photo.type == "photo" and photo.favorite and photo.duration_sec is None
    assert photo.taken_at == "2023-07-14T18:22:05"
    assert photo.location == "Kyoto, Japan" and photo.camera == "FUJIFILM X-T5"
    assert photo.people == ["Anna"] and photo.tags == ["Travel/Japan"]
    assert photo.description == "Sunset"
    assert photo.url == f"https://photos.example.com/photos/{PHOTO_ID}"
    assert video.type == "video" and video.duration_sec == 66 and video.location is None
    assert video.camera == "Canon EOS R5"
    assert new_video.duration_sec == 12


async def test_query_uses_smart_search(mcp_client, fake: FakeImmich):
    await mcp_client.call_tool("search_photos", {"query": "dog on the beach", "limit": 5})

    body = fake.body("/api/search/smart")
    assert body == {"withExif": True, "query": "dog on the beach", "page": 1, "size": 5}
    assert not fake.called("/api/search/metadata")


async def test_query_cannot_be_sorted(mcp_client):
    with pytest.raises(ToolError, match="relevance"):
        await mcp_client.call_tool("search_photos", {"query": "cat", "sort": "oldest"})


async def test_random_sort_uses_random_search(mcp_client, fake: FakeImmich):
    fake.assets = [asset(PHOTO_ID), asset(VIDEO_ID)]

    page = (await mcp_client.call_tool("search_photos", {"sort": "random", "limit": 1})).data

    assert fake.body("/api/search/random")["size"] == 1
    assert page.count == 1 and page.next_page is None


async def test_ambiguous_person_returns_note_without_searching(mcp_client, library):
    page = (await mcp_client.call_tool("search_photos", {"people": ["ann"]})).data

    assert page.photos == []
    assert "several persons: Anna" in page.note and ANNA_SMITH in page.note
    assert not library.called("/api/search/metadata")


async def test_exact_name_beats_partial_matches(mcp_client, library):
    await mcp_client.call_tool("search_photos", {"people": ["ANNA"]})

    assert library.body("/api/search/metadata")["personIds"] == [ANNA]


async def test_ambiguous_tag_leaf_lists_full_paths(mcp_client, library):
    page = (await mcp_client.call_tool("search_photos", {"tags": ["2023"]})).data

    assert "Events/2023 (t3), Travel/2023 (t4)" in page.note


async def test_unknown_city_points_to_list_locations(mcp_client, library):
    page = (await mcp_client.call_tool("search_photos", {"city": "Paris"})).data

    assert page.note == "No city matches 'Paris'. Try list_locations."
    assert not library.called("/api/search/metadata")


async def test_place_names_ignore_accents(mcp_client, library):
    page = (await mcp_client.call_tool("search_photos", {"city": "munchen"})).data

    assert page.resolved == {"city": ["München"]}
    assert library.body("/api/search/metadata")["city"] == "München"


@pytest.mark.parametrize(
    ("value", "end", "expected"),
    [
        ("2023", False, "2023-01-01T00:00:00.000Z"),
        ("2023", True, "2023-12-31T23:59:59.999Z"),
        ("2024-02", True, "2024-02-29T23:59:59.999Z"),
        ("2023-12", True, "2023-12-31T23:59:59.999Z"),
        ("2023-07-14", True, "2023-07-14T23:59:59.999Z"),
        ("2023-07-14T10:30:00", False, "2023-07-14T10:30:00.000Z"),
        ("2023-07-14T10:30:00+03:00", True, "2023-07-14T10:30:00.000+03:00"),
    ],
)
def test_date_bounds(value, end, expected):
    assert _date_bound(value, end=end) == expected


async def test_bad_date_is_a_tool_error(mcp_client):
    with pytest.raises(ToolError, match="Can't parse date 'last summer'"):
        await mcp_client.call_tool("search_photos", {"taken_after": "last summer"})


async def test_view_photos_returns_captioned_images(mcp_client, fake: FakeImmich):
    fake.assets = [asset(PHOTO_ID, "beach.jpg", exifInfo={"city": "Kyoto", "country": "Japan"})]
    fake.thumbnails[PHOTO_ID] = b"webp-bytes"

    result = await mcp_client.call_tool(
        "view_photos", {"asset_ids": [PHOTO_ID, PHOTO_ID, VIDEO_ID], "size": "preview"}
    )

    caption, image, error = result.content
    assert isinstance(caption, TextContent) and "beach.jpg" in caption.text
    assert "Kyoto, Japan" in caption.text and "photos.example.com" in caption.text
    assert isinstance(image, ImageContent) and image.mime_type == "image/webp"
    assert error.text.startswith(f"{VIDEO_ID}: GET /assets/{VIDEO_ID}")
    assert fake.last(f"/api/assets/{PHOTO_ID}/thumbnail").url.params["size"] == "preview"


async def test_view_photos_fails_when_nothing_loads(mcp_client):
    with pytest.raises(ToolError, match="HTTP 400"):
        await mcp_client.call_tool("view_photos", {"asset_ids": [PHOTO_ID]})


async def test_list_people_skips_unnamed_and_pages(mcp_client, library: FakeImmich):
    library.people += [{"id": f"p{i}", "name": f"Person {i}"} for i in range(250)]

    people = (await mcp_client.call_tool("list_people", {"limit": 220})).structured_content["result"]

    assert len(people) == 220 and all(p["name"] for p in people)
    assert [r.url.params["page"] for r in library.requests if r.url.path == "/api/people"] == ["1", "2"]


async def test_list_people_by_name(mcp_client, library):
    people = (await mcp_client.call_tool("list_people", {"query": "anna"})).structured_content
    assert [p["name"] for p in people["result"]] == ["Anna", "Anna Smith"]


async def test_list_tags_and_albums(mcp_client, library):
    tags = (await mcp_client.call_tool("list_tags", {"query": "travel"})).structured_content
    assert [t["value"] for t in tags["result"]] == ["Travel", "Travel/2023", "Travel/Japan"]

    albums = (await mcp_client.call_tool("list_albums", {"query": "summer"})).structured_content
    assert albums["result"][0] == {
        "id": "al2", "name": "Summer 2024", "asset_count": 40,
        "start_date": None, "end_date": None, "shared": False,
    }
    assert albums["result"][1]["start_date"] == "2023-07-01"


async def test_list_locations_scopes_by_resolved_country(mcp_client, library):
    cities = (
        await mcp_client.call_tool("list_locations", {"kind": "city", "country": "jap"})
    ).structured_content["result"]
    assert cities == ["Kyoto", "Tokyo", "Uji"]

    countries = (await mcp_client.call_tool("list_locations", {})).structured_content["result"]
    assert countries == ["Germany", "Japan"]

    with pytest.raises(ToolError, match="No country matches 'France'"):
        await mcp_client.call_tool("list_locations", {"kind": "city", "country": "France"})
