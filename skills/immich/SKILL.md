---
name: immich
description: Find and show photos and videos from the user's Immich library through the immich MCP server - by people (faces), what is in the picture (smart search, "dog on the beach"), tags, albums, places (country/state/city), capture date, favourites, photo vs video, or random. Use for any request about their photos, pictures, videos, albums or memories (фото, фотографии, снимки, видео, альбом, люди, теги, где снято, когда снято, Immich).
compatibility: Requires the immich MCP server (remote HTTP, bearer token) to be connected.
---

# Immich photo library

The `immich` MCP server talks to the user's Immich instance. In opencode its tools show up with the
server prefix (`immich_search_photos`, ...); below they are named without it.

## Tools

| Tool | Use it for |
|---|---|
| `search_photos` | Any photo/video search or listing. Filters combine in one call. |
| `view_photos` | Actually look at up to 8 found photos (images + caption line each). |
| `list_people` | Named people (face recognition); exact names and ids. |
| `list_tags` | Tags as full paths (`Travel/Japan`). |
| `list_albums` | Albums with photo counts and date ranges. |
| `list_locations` | Countries, states or cities where photos were taken. |

### `search_photos` parameters

- `query` - what is in the picture, in natural language (CLIP smart search): `"dog on the beach"`,
  `"snowy mountains"`, `"birthday cake"`. Write it in English: it matches better. Results are ranked
  by relevance, so `sort` can't be combined with `query`.
- `people` - list of names (partial, case-insensitive) or person ids. **AND**: every listed person
  must be in the photo. For "photos of Anna or Bob" make one call per person.
- `tags` - tag names, full paths or ids; AND as well. A parent tag also matches its children
  (`Travel` finds `Travel/Japan`).
- `album` - one album name (partial) or id.
- `country`, `state`, `city` - partial names, accent-insensitive (`munchen` → `München`). Values come
  from Immich reverse geocoding and are usually **English** (`Moscow`, not `Москва`): translate the
  user's place name, or check `list_locations` first.
- `taken_after`, `taken_before` - `YYYY`, `YYYY-MM`, `YYYY-MM-DD` or ISO datetime, both inclusive:
  `taken_after="2023-06", taken_before="2023-08"` covers June-August 2023. Resolve relative dates
  ("last summer", "в прошлом году") yourself from today's date.
- `media_type` - `photo`, `video` or `all` (default). `favorites_only` - only favourites.
- `sort` - `newest` (default), `oldest`, `random` (a new shuffle each call; no paging).
- `limit` (1-100, default 20), `page` (1-based). `next_page` is set when more results exist; fetch
  further pages only when the user needs them.

Check `resolved` in the result: it shows which person / tag / album / place each filter matched. If
a name matched nothing or several values, `photos` is empty and `note` explains it (ambiguous
matches are listed with ids) - pick the intended one and call again with the exact name or id.

## Recipes

**"Photos of Anna in Japan last summer"**:
`search_photos(people=["Anna"], country="Japan", taken_after="2025-06", taken_before="2025-08")`.

**"Find the photo where we had the cake"**: `search_photos(query="birthday cake")`, optionally with
`people` / dates to narrow down; then `view_photos` on the top hits to confirm before answering.

**"Where was I in 2019?"**: `search_photos(taken_after="2019", taken_before="2019", limit=100)`,
then summarise the distinct `location` values (page further if needed), or `list_locations`.

**Who / which tags / which albums exist**: `list_people`, `list_tags`, `list_albums`
(all accept `query` for a partial name).

**Show me**: after a search, call `view_photos(asset_ids=[...])` with a few ids (default small
`thumbnail`; `size="preview"` when details matter, e.g. reading text). Always give the user the
`url` links as well: they open the photo in Immich.

## Rules

- Never invent or guess ids. Asset ids come from `search_photos`; person/tag/album ids from the
  `list_*` tools or a `note`.
- The server is read-only: it can't edit, tag, favourite, move or delete anything. Say so if asked.
- Keep context small: modest `limit`, `view_photos` only for photos you really need to look at
  (each image costs a lot of context), no raw JSON dumps. Show results as a compact table or list:
  date, place, people, file name, link.
- Photos can be private: describe them factually, and don't speculate about who unrecognised
  people are.
