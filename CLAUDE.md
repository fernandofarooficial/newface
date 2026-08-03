# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Flask 3 app that polls a facial recognition API every 30 seconds and persists events to PostgreSQL. Exposes a dark-theme dashboard and a JSON REST API.

## Commands

**Run locally (Windows):**
```
.venv\Scripts\activate
python run.py
```
App starts on `http://localhost:5006`.

**Run locally (Linux/VPS):**
```
source .venv/bin/activate
python run.py
```

**Install dependencies:**
```
pip install -r requirements.txt
```

**Production (VPS 72.60.58.241):**
```bash
cd /var/www/newface
git pull
systemctl restart newface          # gunicorn managed by systemd
```
Logs: `/var/log/newface/app.log`, `/var/log/newface/access.log`

**Database migrations:** run SQL files manually via psql or DBeaver:
```bash
psql -U fefa_dev -h localhost -d lojas -f migrations/002_add_face_image_url.sql
```

## Architecture

### Request flow
Browser → nginx (`/newface/` location) → gunicorn `:5006` → Flask

nginx strips the `/newface/` prefix before forwarding. `ProxyFix` middleware in `app/__init__.py` reads `X-Forwarded-Prefix: /newface` and sets `request.script_root`, which the Jinja template renders into `const BASE = '/newface'` so all JS `fetch()` calls use the correct paths.

### App factory (`app/__init__.py`)
Creates the Flask app, applies `ProxyFix`, initialises SQLAlchemy, registers the blueprint, then starts an APScheduler `BackgroundScheduler` that fires `collect_events(app)` every `FACIAL_POLL_SECS` seconds (default 30). The scheduler runs in a daemon thread — gunicorn's `sync` worker class is required (async workers would break it).

### Collector (`app/collector.py`)
Calls `GET /api/face-events` on the facial API with HTTP Basic Auth. For each event:
- Skips if `event_id` already exists; backfills `face_image_url` if the existing row has it NULL.
- Upserts `Pessoa` (person) with demographic data.
- Inserts `EventoFacial` + child `EventoMatch` rows in a single transaction.
- Updates `SyncControl` with the run summary.

### Models (`app/models.py`)
Six SQLAlchemy models all under schema `itumbiara` in database `lojas`:
- `Estabelecimento` → `Camera` (one-to-many)
- `Pessoa` → `EventoFacial` (one-to-many)
- `EventoFacial` → `EventoMatch` (one-to-many, cascade delete)
- `SyncControl` — single-row audit log updated after every collection run

`EventoFacial.to_dict(match_images=None)` takes a `{event_id: face_image_url}` lookup dict so callers can attach the photo of the *referenced* match event without an N+1 query — see `_build_event_filters`/`match_images` batching in `routes.py`.

### Access control (`app/routes.py`)
The whole blueprint sits behind a password gate enforced by `bp.before_request` (`require_login`): unauthenticated browser requests redirect to `/login`, unauthenticated `/api/*` requests get a `401 {"error": "unauthorized"}` JSON body. Only `main.login_page`/`main.login_submit` (`PUBLIC_ENDPOINTS`) are reachable without a session.

Auth is a flat access-code → camera-scope map, `Config.ACCESS_CODES` (env-overridable via `ACCESS_CODE_ALL`/`ACCESS_CODE_CAM12`/`ACCESS_CODE_CAM5`, defaults `xt05`→all, `9126`→`["cam_1","cam_2"]`, `4239`→`["cam_5"]`). `POST /login` looks the submitted `codigo` up in that dict and stores the resulting scope in the signed session cookie as `session["allowed_cameras"]` (`"*"` for all, else a list of `camera_id` strings) — there's no per-user identity, just a shared code per camera group. `GET /logout` clears the session. `_allowed_cameras()` reads that scope back (`None` = unrestricted) and every data query that can be scoped by camera ANDs in `EventoFacial.camera_id.in_(allowed)` (or the `Pessoa.eventos.any(...)` equivalent) *in addition to* any explicit `camera_id` query-string filter — so a scoped session can't see another camera's data even by hand-crafting the URL. This touches `/api/status` (event/person counts), `/api/eventos`, `/api/pessoas` (only people with an event on an allowed camera), `_build_event_filters` (shared by `/api/pessoas-eventos`), `/api/tabuleiro`, and `/api/cameras` (dropdown only lists allowed cameras). `/api/estabelecimentos` and `/api/face-image` are NOT camera-scoped (store names and the image proxy aren't considered camera-identifying).

### Routes (`app/routes.py`)
| Method | URL | Notes |
|--------|-----|-------|
| GET/POST | `/login` | Password form; POST checks `codigo` against `Config.ACCESS_CODES` and sets the session's camera scope |
| GET | `/logout` | Clears the session |
| GET | `/` | Renders the dashboard (`index.html`) — requires an authenticated session |
| GET | `/api/status` | Sync status + aggregate stats (total eventos/pessoas, novos/conhecidos) |
| GET | `/api/eventos` | Flat event list, filterable by `camera_id`, `store_id`, `match_type`, `pessoa_id` (repeatable — `?pessoa_id=1&pessoa_id=2` — matches any of them via `IN`), `date_from`/`date_to`, `has_matches` |
| GET | `/api/pessoas-eventos` | **Powers the "Eventos Recentes" tab.** Groups events by `Pessoa` (most-recently-detected person first, max 20 events each), reusing the same filters as `/api/eventos` via `_build_event_filters`. Capped at 50 people when no `pessoa_id` is given; when one or more `pessoa_id` are passed, all matching people are returned uncapped since the caller already narrowed the set. Each item is `{pessoa, eventos: [...]}` |
| GET | `/api/pessoas` | Person list for the "Pessoas" tab and the person filter dropdown. Optional `q` does a case-insensitive substring match against `person_unique_id` OR `nome` (e.g. `q=cli_0026`), used by the dropdown's live search so people aren't limited to whatever page was preloaded |
| POST | `/api/pessoas/<id>/nome` | Inline-edit a person's display name from the dashboard |
| POST | `/api/coletar` | Triggers `collect_events` synchronously (the "Coletar Agora" button) |
| GET | `/api/cameras`, `/api/estabelecimentos` | Populate the filter dropdowns |
| GET | `/api/face-image` | Auth proxy, see below |
| GET | `/api/tabuleiro` | **Powers the "Tabuleiro" tab.** Returns a page of up to 120 people (`TABULEIRO_LIMIT`, fixed page size) via `offset`, one card per `Pessoa`, optionally filtered by `camera_id` (only people with at least one event on that camera). Sortable via `sort` (`ultima_deteccao` default or `primeira_deteccao`), always desc. Response includes `total`/`limit`/`offset` for pagination. Returns each person's *first-ever* photo (`primeira_face_image_url`), found with a `ROW_NUMBER() OVER (PARTITION BY pessoa_id ORDER BY timestamp_evento ASC)` window query scoped to the returned person IDs — avoids N+1 lookups. The camera filter narrows *which people* appear but the photo shown is still their real first-ever face, not their first appearance on that camera |

### Face image proxy (`app/routes.py` — `/api/face-image`)
The facial API requires Basic Auth even for image URLs. The proxy route validates that `path` starts with `/media/`, then fetches the image server-side with credentials and streams it back. The JS builds the `<img src>` using `BASE + '/api/face-image?path=' + encodeURIComponent(face_image_url)`.

### Frontend (`app/templates/base.html`, `app/templates/index.html`, `app/templates/login.html`)
No build step and no separate JS/CSS files — `app/static/js` and `app/static/css` are empty; all styling and behaviour live inline in `<style>`/`<script>` blocks inside the templates. `login.html` is a standalone page (doesn't extend `base.html`, no tabs/header) with a single password field that POSTs to `/login`. `base.html`'s header has a "Sair" link (`GET /logout`) next to the sync badge. `index.html` renders three tabs:
- **Eventos Recentes** — one row per person (`renderPessoaRow`), each showing that person's events as photo cards (`renderEventoCard`). A card is "dual" (shows two photos side by side) when its best match's `face_image_url` hasn't already been shown as a primary photo or as another card's match reference in the same row — this de-duplication (`primaryUrls`/`shownMatchUrls` sets in `renderPessoaRow`) is what the recent "card duplicado" bugfix commits target. Filters (date range, pessoa, estabelecimento, câmera, has-matches) re-query `/api/pessoas-eventos`. The pessoa filter is a custom checkbox dropdown (`#f-pessoa-wrap`/`renderPessoaOptions`/`onPessoaCheckChange`) backed by a `selectedPessoaIds` Set — not a native `<select>` — so multiple people can be selected at once; each selected id is appended as a repeated `pessoa_id` query param. Typing in the dropdown's search box (`onPessoaSearchInput`, 250ms debounce) calls `GET /api/pessoas?q=...` server-side rather than filtering a preloaded list, so any of the 200+ people can be found by partial `person_unique_id` (e.g. `cli_0026`) or name — not just whichever page happened to be cached. A `pessoaCache` Map remembers `{id: pessoa}` for anyone ever returned by a search so the button label can still show a selected person's name after the dropdown's visible result set has moved on.
- **Pessoas** — flat table with inline name editing (`salvarNome` → `POST /api/pessoas/:id/nome`).
- **Tabuleiro** — grid of one card per person (`tabuleiroCard`) showing their first-ever photo, event count, and detection timestamp; pages of up to 120 people per fetch (`loadTabuleiro(offset)`), with Anterior/Próxima controls (`renderTabuleiroPagination`) that only render when `total` exceeds the 120-person page size. Filterable by camera (`#f-tab-camera` → `camera_id`) and sortable by last appearance or person creation order (`#f-tab-sort` → `sort` param); changing either resets to offset 0. Lazy-loaded: `activateTabuleiro()` only fires the first `/api/tabuleiro` fetch when the tab is clicked.

Polling: `loadStatus()` + `loadEventos()` re-run every 60s via `setInterval`; this is independent of the server-side `FACIAL_POLL_SECS` collector interval.

### Configuration (`app/config.py`)
All settings read from `.env` via `python-dotenv`. Key variables: `DB_HOST`, `DB_NAME` (`lojas`, lowercase), `DB_SCHEMA` (`itumbiara`), `FACIAL_API_BASE`, `FACIAL_POLL_SECS`, `FACIAL_LIMIT` (events fetched per collector run), `FACIAL_MATCHES_LIM` (historical matches fetched per event), `ACCESS_CODE_ALL`/`ACCESS_CODE_CAM12`/`ACCESS_CODE_CAM5` (see Access control above). The SQLAlchemy URI passes `search_path` via the `options` query param. Every setting has a hardcoded fallback default (including DB/API credentials) if `.env` is missing — fine for this single-deployment app, but don't copy that pattern into a multi-environment project. `SECRET_KEY` now also signs the login session cookie, not just a Flask boilerplate default — make sure it's set to something real in production `.env`, not the `dev-secret-change-in-production` fallback.

## Infrastructure

| Component | Detail |
|-----------|--------|
| VPS / DB host | `72.60.58.241` — PostgreSQL runs on the same machine as the app |
| DB | `lojas` (lowercase), schema `itumbiara`, user `fefa_dev` |
| Facial API | `http://201.71.234.84:8000` — Basic Auth `admin / admin@facial26` |
| Nginx config | `/etc/nginx/sites-enabled/apps` — `location /newface/` block |
| Systemd service | `/etc/systemd/system/newface.service` |
| Python | 3.12 on VPS, 3.13 locally — `psycopg2-binary>=2.9.12` required for 3.13 |
