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

### Face image proxy (`app/routes.py` — `/api/face-image`)
The facial API requires Basic Auth even for image URLs. The proxy route validates that `path` starts with `/media/`, then fetches the image server-side with credentials and streams it back. The JS builds the `<img src>` using `BASE + '/api/face-image?path=' + encodeURIComponent(face_image_url)`.

### Configuration (`app/config.py`)
All settings read from `.env` via `python-dotenv`. Key variables: `DB_HOST`, `DB_NAME` (`lojas`, lowercase), `DB_SCHEMA` (`itumbiara`), `FACIAL_API_BASE`, `FACIAL_POLL_SECS`. The SQLAlchemy URI passes `search_path` via the `options` query param.

## Infrastructure

| Component | Detail |
|-----------|--------|
| VPS / DB host | `72.60.58.241` — PostgreSQL runs on the same machine as the app |
| DB | `lojas` (lowercase), schema `itumbiara`, user `fefa_dev` |
| Facial API | `http://201.71.234.84:8000` — Basic Auth `admin / admin@facial26` |
| Nginx config | `/etc/nginx/sites-enabled/apps` — `location /newface/` block |
| Systemd service | `/etc/systemd/system/newface.service` |
| Python | 3.12 on VPS, 3.13 locally — `psycopg2-binary>=2.9.12` required for 3.13 |
