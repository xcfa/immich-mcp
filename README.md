# immich-mcp

MCP-сервер для [Immich](https://immich.app/) на Python + [FastMCP](https://gofastmcp.com) и skill, описывающий работу с ним.

Возможности (только чтение):
- поиск фото и видео по людям (распознанные лица), тегам, альбому, месту (страна / регион / город), дате съёмки, избранному, типу (фото или видео). Фильтры комбинируются;
- поиск по содержимому кадра на естественном языке («собака на пляже»). Используется smart search Immich (CLIP);
- сортировка: сначала новые, сначала старые, случайные;
- просмотр найденных фото: модель получает превью как изображения;
- справочники: люди, теги, альбомы, места съёмки.

Сервер работает через REST API Immich (`/api/*`) с API-ключом.

## Инструменты

| Инструмент | Назначение |
|---|---|
| `search_photos` | Поиск. Параметры: `query` (содержимое кадра), `people`, `tags`, `album`, `country`, `state`, `city`, `taken_after`, `taken_before`, `media_type` (`all`, `photo`, `video`), `favorites_only`, `sort` (`newest`, `oldest`, `random`), `limit`, `page` |
| `view_photos` | До 8 фото как изображения (`thumbnail` ≈250px или `preview` ≈1440px) с подписью: дата, место, люди, ссылка |
| `list_people` | Именованные люди. Безымянные кластеры лиц пропускаются |
| `list_tags` | Теги полными путями (`Travel/Japan`) |
| `list_albums` | Альбомы: число фото и диапазон дат |
| `list_locations` | Страны, регионы или города, где сделаны снимки |

Имена людей, тегов, альбомов и мест можно передавать частично, без учёта регистра и диакритики (`munchen` → `München`). Сервер сам сопоставляет их с точными значениями Immich. Что именно совпало, он возвращает в поле `resolved`. Если совпадений нет или их несколько, поиск не выполняется, а в `note` перечислены кандидаты с id.

Как фильтры ведут себя в Immich:
- `people` и `tags` работают как И: на фото должны быть все перечисленные люди и теги. Родительский тег находит и дочерние;
- в `taken_after` / `taken_before` можно передать `YYYY`, `YYYY-MM`, `YYYY-MM-DD` или ISO datetime. Обе границы включительно: `taken_before="2023-08"` означает «до конца августа 2023». Даты без часового пояса считаются в UTC;
- названия мест берутся из обратного геокодирования Immich, обычно на английском (`Moscow`);
- запрос `query` требует включённого машинного обучения в Immich. Результаты идут по релевантности, поэтому `sort` с `query` не сочетается.

Ссылки в результатах ведут на `<IMMICH_PUBLIC_URL>/photos/<id>`.

## Запуск в Docker

```bash
cp .env.example .env   # заполнить IMMICH_URL, IMMICH_API_KEY, MCP_AUTH_TOKEN
docker compose up -d --build
```

| Переменная | Описание |
|---|---|
| `IMMICH_URL` | Адрес Immich, доступный из контейнера (например `http://immich-server:2283`) |
| `IMMICH_API_KEY` | API-ключ: Immich → Account Settings → API Keys. Нужны права `asset.read`, `asset.view`, `person.read`, `tag.read`, `album.read` (или `all`). Поиск идёт от имени владельца ключа |
| `IMMICH_PUBLIC_URL` | Адрес Immich для ссылок, которые увидит пользователь. По умолчанию `IMMICH_URL` |
| `MCP_AUTH_TOKEN` | Bearer-токен для MCP-клиентов, сгенерировать: `openssl rand -hex 32`. Без него сервер не запускается |
| `MCP_PORT` | Порт, по умолчанию `8000` |
| `IMMICH_TIMEOUT` | Таймаут запросов к Immich в секундах, по умолчанию `30` |

- MCP endpoint: `http://<host>:8000/mcp`. Нужен заголовок `Authorization: Bearer <MCP_AUTH_TOKEN>`.
- Healthcheck: `GET /health`, без авторизации.

Токен проверяется на уровне HTTP-транспорта по стандартной схеме авторизации MCP. Заголовок подставляет MCP-клиент, в контекст модели он не попадает.

Готовый образ публикуется в `ghcr.io/xcfa/immich-mcp` при пуше тега `vX.Y.Z` (см. `.github/workflows/publish.yml`).

## Подключение к opencode

В `opencode.json` (проектный или `~/.config/opencode/opencode.json`):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "immich": {
      "type": "remote",
      "url": "http://localhost:8000/mcp",
      "enabled": true,
      "oauth": false,
      "headers": {
        "Authorization": "Bearer {env:IMMICH_MCP_TOKEN}"
      }
    }
  }
}
```

- `IMMICH_MCP_TOKEN` — переменная окружения со значением `MCP_AUTH_TOKEN`.
- `"oauth": false` отключает OAuth-discovery, раз используется статический токен.
- Skill: скопировать `skills/immich` в `~/.config/opencode/skills/immich/` (глобально) или в `.opencode/skills/immich/` (в проект).

## Подключение к Claude Code

```bash
claude mcp add --transport http immich http://localhost:8000/mcp --header "Authorization: Bearer $IMMICH_MCP_TOKEN"
```

Skill: скопировать `skills/immich` в `~/.claude/skills/immich/`.

Если navidrome-mcp запущен на том же хосте, задайте одному из серверов другой порт: `MCP_PORT` и проброс в `docker-compose.yml`.

## Разработка

```bash
uv sync
uv run pytest
uv run immich-mcp   # читает .env из текущей директории
```

Тесты используют поддельный Immich на `httpx.MockTransport` и in-memory клиент FastMCP. Живой сервер для них не нужен.

## Релиз

1. Поднять `version` в `pyproject.toml` и закоммитить.
2. `git tag vX.Y.Z && git push origin master --tags`.
3. Workflow прогонит тесты, сверит тег с версией и опубликует multi-arch образ (`amd64`, `arm64`) в GHCR с тегами `X.Y.Z`, `X.Y` и `latest`.
