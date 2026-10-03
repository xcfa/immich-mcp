"""FastMCP server exposing Immich photo search by people, tags, albums, places, dates and content."""

import asyncio
import re
import unicodedata
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

import httpx
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.lifespan import lifespan
from fastmcp.tools import ToolResult
from fastmcp.utilities.types import Image
from mcp.types import TextContent
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from immich_mcp import __version__
from immich_mcp.auth import SharedSecretVerifier
from immich_mcp.client import Body, ImmichClient, ImmichError
from immich_mcp.config import Settings
from immich_mcp.models import Album, Person, Photo, PhotoPage, Tag

MAX_VIEW = 8
PEOPLE_PAGE = 200
MAX_PEOPLE_PAGES = 10
MEDIA_TYPE_FILTER = {"photo": "IMAGE", "video": "VIDEO"}

INSTRUCTIONS = """\
Access to the user's Immich photo library.
Use search_photos to find photos and videos by content (free-text query), people, tags, album,
place (country/state/city) and capture date; filters combine with AND. Use list_people,
list_tags, list_albums and list_locations to discover exact names. view_photos shows the
found photos to you as images. Asset ids come from search results - never invent ids."""

DateBound = Annotated[
    str | None,
    Field(description="YYYY, YYYY-MM, YYYY-MM-DD or a full ISO datetime"),
]


class Unresolved(Exception):
    """A filter value matched no library value, or several of them."""


def _client(ctx: Context) -> ImmichClient:
    return ctx.lifespan_context["immich"]


def _web_url(ctx: Context) -> str:
    return ctx.lifespan_context["web_url"]


def create_server(
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastMCP:
    web_url = (settings.immich_public_url or settings.immich_url).rstrip("/")

    @lifespan
    async def immich_lifespan(server: FastMCP) -> AsyncIterator[dict]:
        client = ImmichClient(
            settings.immich_url,
            settings.immich_api_key.get_secret_value(),
            timeout=settings.immich_timeout,
            transport=transport,
        )
        try:
            yield {"immich": client, "web_url": web_url}
        finally:
            await client.aclose()

    mcp = FastMCP(
        "immich",
        instructions=INSTRUCTIONS,
        version=__version__,
        auth=SharedSecretVerifier(settings.mcp_auth_token.get_secret_value()),
        lifespan=immich_lifespan,
    )

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "version": __version__})

    # --- photos -----------------------------------------------------------

    @mcp.tool(annotations={"readOnlyHint": True})
    async def search_photos(
        ctx: Context,
        query: Annotated[
            str | None,
            Field(
                description="What is in the picture, in natural language (smart/CLIP search), "
                "e.g. 'dog on the beach', 'birthday cake'. Results are ranked by relevance"
            ),
        ] = None,
        people: Annotated[
            list[str] | None,
            Field(description="Person names or ids; every listed person must be in the photo"),
        ] = None,
        tags: Annotated[
            list[str] | None,
            Field(
                description="Tag names, full paths ('Travel/Japan') or ids; all must match. "
                "A parent tag also matches its child tags"
            ),
        ] = None,
        album: Annotated[str | None, Field(description="Album name (partial) or id")] = None,
        country: Annotated[str | None, Field(description="Country name as stored in Immich")] = None,
        state: Annotated[str | None, Field(description="State / region / province")] = None,
        city: Annotated[str | None, Field(description="City name as stored in Immich")] = None,
        taken_after: DateBound = None,
        taken_before: DateBound = None,
        media_type: Literal["all", "photo", "video"] = "all",
        favorites_only: bool = False,
        sort: Annotated[
            Literal["newest", "oldest", "random"] | None,
            Field(description="Default newest; not allowed with query (relevance order)"),
        ] = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
        page: Annotated[int, Field(ge=1, description="1-based page number")] = 1,
    ) -> PhotoPage:
        """Find photos and videos. All filters are optional and combine with AND.

        Names of people, tags, albums and places may be partial and are case-insensitive;
        `resolved` shows what they matched."""
        client = _client(ctx)
        if query and sort:
            raise ToolError("Results of a content query are ranked by relevance; drop sort or query.")

        body: Body = {"withExif": True}
        resolved: dict[str, list[str]] = {}
        try:
            if people:
                body["personIds"] = await _resolve_people(client, people, resolved)
            if tags:
                body["tagIds"] = await _resolve_tags(client, tags, resolved)
            if album:
                body["albumIds"] = [await _resolve_album(client, album, resolved)]
            body |= await _resolve_places(
                client, resolved, country=country, state=state, city=city
            )
        except Unresolved as exc:
            return PhotoPage(count=0, page=page, photos=[], resolved=resolved, note=str(exc))

        if taken_after:
            body["takenAfter"] = _date_bound(taken_after, end=False)
        if taken_before:
            body["takenBefore"] = _date_bound(taken_before, end=True)
        if media_type != "all":
            body["type"] = MEDIA_TYPE_FILTER[media_type]
        if favorites_only:
            body["isFavorite"] = True

        next_page: Any = None
        if query:
            assets = await client.search_smart(body | {"query": query, "page": page, "size": limit})
            items, next_page = assets["items"], assets.get("nextPage")
        elif sort == "random":
            # Every call is a new shuffle; random results are not paged.
            items = await client.search_random(body | {"size": limit, "withPeople": True})
        else:
            assets = await client.search_metadata(
                body
                | {
                    "order": "asc" if sort == "oldest" else "desc",
                    "page": page,
                    "size": limit,
                    "withPeople": True,
                }
            )
            items, next_page = assets["items"], assets.get("nextPage")

        web_url = _web_url(ctx)
        return PhotoPage(
            count=len(items),
            page=page,
            next_page=int(next_page) if next_page else None,
            photos=[Photo.from_api(a, web_url) for a in items],
            resolved=resolved,
        )

    @mcp.tool(annotations={"readOnlyHint": True})
    async def view_photos(
        ctx: Context,
        asset_ids: Annotated[
            list[str],
            Field(
                min_length=1,
                max_length=MAX_VIEW,
                description="Asset ids taken from search_photos results",
            ),
        ],
        size: Annotated[
            Literal["thumbnail", "preview"],
            Field(description="thumbnail = small (~250px), preview = large (~1440px)"),
        ] = "thumbnail",
    ) -> ToolResult:
        """Look at photos: returns each one as an image with a caption line.

        Videos are shown as a still frame. Use it to check or describe what is in a picture."""
        client, web_url = _client(ctx), _web_url(ctx)

        async def one(asset_id: str) -> list[Any]:
            try:
                raw, (data, mime) = await asyncio.gather(
                    client.asset(asset_id), client.thumbnail(asset_id, size)
                )
            except ImmichError as exc:
                return [TextContent(type="text", text=f"{asset_id}: {exc}")]
            image_format = mime.split(";")[0].split("/")[-1].strip() or "jpeg"
            caption = _caption(Photo.from_api(raw, web_url))
            return [TextContent(type="text", text=caption), Image(data=data, format=image_format)]

        parts = await asyncio.gather(*(one(i) for i in _dedupe(asset_ids)))
        if not any(len(p) > 1 for p in parts):
            raise ToolError("; ".join(p[0].text for p in parts))
        return ToolResult(content=[block for part in parts for block in part])

    # --- discovery --------------------------------------------------------

    @mcp.tool(annotations={"readOnlyHint": True})
    async def list_people(
        ctx: Context,
        query: Annotated[str | None, Field(description="Partial person name")] = None,
        limit: Annotated[int, Field(ge=1, le=500)] = 50,
    ) -> list[Person]:
        """List named people recognized in the library (unnamed face clusters are skipped)."""
        client = _client(ctx)
        if query:
            found = await client.search_people(query)
        else:
            found = []
            for page in range(1, MAX_PEOPLE_PAGES + 1):
                data = await client.people(page=page, size=PEOPLE_PAGE)
                found += data.get("people") or []
                if not data.get("hasNextPage") or len(_named(found)) >= limit:
                    break
        return [Person.from_api(p) for p in _named(found)][:limit]

    @mcp.tool(annotations={"readOnlyHint": True})
    async def list_tags(
        ctx: Context,
        query: Annotated[str | None, Field(description="Partial tag name or path")] = None,
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
    ) -> list[Tag]:
        """List tags in the library, as full paths ('Travel/Japan')."""
        tags = await _client(ctx).tags()
        if query:
            tags = [t for t in tags if _fold(query) in _fold(t.get("value") or "")]
        tags.sort(key=lambda t: _fold(t.get("value") or ""))
        return [Tag(id=t["id"], value=t["value"]) for t in tags[:limit]]

    @mcp.tool(annotations={"readOnlyHint": True})
    async def list_albums(
        ctx: Context,
        query: Annotated[str | None, Field(description="Partial album name")] = None,
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
    ) -> list[Album]:
        """List albums owned by or shared with the user, with photo counts and date ranges."""
        albums = await _client(ctx).albums()
        if query:
            albums = [a for a in albums if _fold(query) in _fold(a.get("albumName") or "")]
        albums.sort(key=lambda a: _fold(a.get("albumName") or ""))
        return [Album.from_api(a) for a in albums[:limit]]

    @mcp.tool(annotations={"readOnlyHint": True})
    async def list_locations(
        ctx: Context,
        kind: Annotated[
            Literal["country", "state", "city"], Field(description="Which level to list")
        ] = "country",
        country: Annotated[str | None, Field(description="Only places in this country")] = None,
        state: Annotated[str | None, Field(description="Only cities in this state")] = None,
        query: Annotated[str | None, Field(description="Partial name filter")] = None,
        limit: Annotated[int, Field(ge=1, le=1000)] = 200,
    ) -> list[str]:
        """List countries, states or cities where the user's photos were taken.

        Names come from Immich reverse geocoding (usually in English, e.g. 'Moscow')."""
        client = _client(ctx)
        scope = {
            "country": country if kind in ("state", "city") else None,
            "state": state if kind == "city" else None,
        }
        try:
            places = await _resolve_places(client, {}, **scope)
        except Unresolved as exc:
            raise ToolError(str(exc)) from exc
        values = await client.suggestions(kind, **places)
        if query:
            values = [v for v in values if _fold(query) in _fold(v)]
        return sorted(set(values), key=_fold)[:limit]

    return mcp


# --- filter resolution ----------------------------------------------------


def _fold(value: str) -> str:
    """Case- and accent-insensitive form for matching user input against library values."""
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _is_id(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def _pick(label: str, value: str, items: list[Body], keys: tuple[str, ...], hint: str) -> Body:
    """The single item whose key equals `value`, else the single one containing it."""
    wanted = _fold(value)
    for matches in (str.__eq__, str.__contains__):
        hits = [i for i in items if any(matches(_fold(i.get(k) or ""), wanted) for k in keys)]
        if len(hits) == 1:
            return hits[0]
        if hits:
            names = ", ".join(
                f"{h[keys[0]]} ({h['id']})" if "id" in h else h[keys[0]] for h in hits[:15]
            )
            more = f" and {len(hits) - 15} more" if len(hits) > 15 else ""
            retry = "a more specific name or one of the ids" if "id" in hits[0] else "a more specific name"
            raise Unresolved(f"'{value}' matches several {label}s: {names}{more}. Pass {retry}.")
    raise Unresolved(f"No {label} matches '{value}'. Try {hint}.")


async def _resolve_people(
    client: ImmichClient, names: list[str], resolved: dict[str, list[str]]
) -> list[str]:
    ids: list[str] = []
    for name in names:
        if _is_id(name):
            ids.append(name)
            continue
        person = _pick("person", name, await client.search_people(name), ("name",), "list_people")
        ids.append(person["id"])
        resolved.setdefault("people", []).append(person["name"])
    return _dedupe(ids)


async def _resolve_tags(
    client: ImmichClient, names: list[str], resolved: dict[str, list[str]]
) -> list[str]:
    all_tags = await client.tags()
    ids: list[str] = []
    for name in names:
        if _is_id(name):
            ids.append(name)
            continue
        tag = _pick("tag", name, all_tags, ("value", "name"), "list_tags")
        ids.append(tag["id"])
        resolved.setdefault("tags", []).append(tag["value"])
    return _dedupe(ids)


async def _resolve_album(client: ImmichClient, name: str, resolved: dict[str, list[str]]) -> str:
    if _is_id(name):
        return name
    album = _pick("album", name, await client.albums(), ("albumName",), "list_albums")
    resolved["album"] = [album["albumName"]]
    return album["id"]


async def _resolve_places(
    client: ImmichClient,
    resolved: dict[str, list[str]],
    *,
    country: str | None = None,
    state: str | None = None,
    city: str | None = None,
) -> dict[str, str]:
    """Map partial place names onto exact Immich values, each level scoped by the previous."""
    places: dict[str, str] = {}
    for kind, value in (("country", country), ("state", state), ("city", city)):
        if not value:
            continue
        options = await client.suggestions(
            kind, country=places.get("country"), state=places.get("state")
        )
        found = _pick(kind, value, [{"name": o} for o in options], ("name",), "list_locations")
        places[kind] = found["name"]
        resolved[kind] = [found["name"]]
    return places


def _date_bound(value: str, *, end: bool) -> str:
    """Start (or end, inclusive) of a year, month, day or exact moment, as an ISO timestamp."""
    text = value.strip()
    try:
        if re.fullmatch(r"\d{4}", text):
            start, after = datetime(int(text), 1, 1), datetime(int(text) + 1, 1, 1)
        elif m := re.fullmatch(r"(\d{4})-(\d{1,2})", text):
            year, month = int(m[1]), int(m[2])
            start = datetime(year, month, 1)
            after = datetime(year + month // 12, month % 12 + 1, 1)
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            start = datetime.fromisoformat(text)
            after = start + timedelta(days=1)
        else:
            moment = datetime.fromisoformat(text)
            if moment.tzinfo:
                return moment.isoformat(timespec="milliseconds")
            return moment.isoformat(timespec="milliseconds") + "Z"
    except ValueError as exc:
        raise ToolError(
            f"Can't parse date '{value}'; use YYYY, YYYY-MM, YYYY-MM-DD or an ISO datetime."
        ) from exc
    bound = after - timedelta(milliseconds=1) if end else start
    return bound.isoformat(timespec="milliseconds") + "Z"


# --- helpers --------------------------------------------------------------


def _named(people: list[Body]) -> list[Body]:
    return [p for p in people if p.get("name") and not p.get("isHidden")]


def _caption(photo: Photo) -> str:
    parts = [photo.id, photo.file_name, photo.type, photo.taken_at, photo.location]
    if photo.people:
        parts.append("people: " + ", ".join(photo.people))
    parts.append(photo.url)
    return " | ".join(p for p in parts if p)


def _dedupe(ids: list[str]) -> list[str]:
    return list(dict.fromkeys(ids))


def main() -> None:
    settings = Settings()
    create_server(settings).run(
        transport="http", host=settings.mcp_host, port=settings.mcp_port
    )


if __name__ == "__main__":
    main()
