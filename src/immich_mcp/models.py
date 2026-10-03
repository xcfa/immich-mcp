"""Compact output models. Immich responses are large; tools return only what an LLM needs."""

from typing import Any

from pydantic import BaseModel, Field

MEDIA_TYPES = {"IMAGE": "photo", "VIDEO": "video"}


def _local_time(value: str | None) -> str | None:
    # `localDateTime` is wall-clock time at the place of capture, serialized with a fake `Z`.
    if not value:
        return None
    return value.removesuffix("Z").split(".")[0]


def _duration_sec(value: Any) -> int | None:
    # Immich < 3 sends "H:MM:SS.ffffff" (zero for images), newer versions milliseconds or null.
    if isinstance(value, int | float):
        seconds = value / 1000
    elif isinstance(value, str) and value.count(":") == 2:
        h, m, s = value.split(":")
        seconds = int(h) * 3600 + int(m) * 60 + float(s)
    else:
        return None
    return round(seconds) or None


def _join(*parts: str | None) -> str | None:
    # City and state are often the same ("Kyoto, Kyoto, Japan"); keep each name once.
    return ", ".join(dict.fromkeys(p for p in parts if p)) or None


def _camera(make: str | None, model: str | None) -> str | None:
    # Many cameras repeat the make in the model ("Canon" / "Canon EOS R5").
    if make and model and model.casefold().startswith(make.casefold()):
        return model
    return " ".join(p for p in (make, model) if p) or None


class Photo(BaseModel):
    id: str
    type: str
    file_name: str
    taken_at: str | None = Field(default=None, description="Local time where it was taken")
    location: str | None = None
    people: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    favorite: bool = False
    description: str | None = None
    camera: str | None = None
    duration_sec: int | None = None
    url: str

    @classmethod
    def from_api(cls, raw: dict[str, Any], web_url: str) -> "Photo":
        exif = raw.get("exifInfo") or {}
        return cls(
            id=raw["id"],
            type=MEDIA_TYPES.get(raw.get("type", ""), str(raw.get("type", "")).lower()),
            file_name=raw.get("originalFileName", ""),
            taken_at=_local_time(raw.get("localDateTime") or raw.get("fileCreatedAt")),
            location=_join(exif.get("city"), exif.get("state"), exif.get("country")),
            people=[p["name"] for p in raw.get("people") or [] if p.get("name")],
            tags=[t["value"] for t in raw.get("tags") or [] if t.get("value")],
            favorite=bool(raw.get("isFavorite")),
            description=exif.get("description") or None,
            camera=_camera(exif.get("make"), exif.get("model")),
            duration_sec=_duration_sec(raw.get("duration")),
            url=f"{web_url}/photos/{raw['id']}",
        )


class PhotoPage(BaseModel):
    count: int
    page: int
    next_page: int | None = None
    photos: list[Photo]
    resolved: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Library values the people/tag/album/location filters matched",
    )
    note: str | None = None


class Person(BaseModel):
    id: str
    name: str
    birth_date: str | None = None

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> "Person":
        return cls(id=raw["id"], name=raw["name"], birth_date=raw.get("birthDate"))


class Tag(BaseModel):
    id: str
    value: str = Field(description="Full tag path, e.g. 'Travel/Japan'")


class Album(BaseModel):
    id: str
    name: str
    asset_count: int = 0
    start_date: str | None = None
    end_date: str | None = None
    shared: bool = False

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> "Album":
        return cls(
            id=raw["id"],
            name=raw.get("albumName", ""),
            asset_count=raw.get("assetCount") or 0,
            start_date=(raw.get("startDate") or "")[:10] or None,
            end_date=(raw.get("endDate") or "")[:10] or None,
            shared=bool(raw.get("shared")),
        )
