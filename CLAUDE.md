# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Book Knook ("BooKnook") — a shared to-be-read/reading tracker that layers on top of a
[Grimmory](../grimmory-tools/grimmory/) (self-hosted library, BookLore-family) instance. There's no
independent user store or book catalog of its own: every user logs in with their real Grimmory
username/password, and reading status (Currently Reading / Finished) is derived automatically from
each user's Grimmory reading sessions rather than set by hand. Internal env vars/cookie names still
say `TBR_*` (To Be Read) from before the app was renamed — that's expected, not a bug.

Two parts: a FastAPI JSON API in `app/` and a SvelteKit SPA in `frontend/`. In production they run
as **one process, one Docker image** — FastAPI serves the SvelteKit static build directly (see
`app/main.py`'s `spa_fallback`), so this stays a single self-hostable container alongside a Grimmory
instance, not a two-service deployment.

## Commands

```bash
# Backend (from repo root; note the `app.main:app` module path)
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
export TBR_SECRET_KEY=dev-secret GRIMMORY_BASE_URL=https://your-grimmory-instance
uvicorn app.main:app --reload

# Backend tests (pytest, no config file — just run from repo root)
pytest
pytest tests/test_api.py
pytest tests/test_api.py::test_api_login_success_sets_cookie_and_returns_me
pytest -k library_check

# Frontend (separate terminal; proxies /api, /covers, /health to the backend above — see
# frontend/vite.config.ts)
cd frontend
npm install
npm run dev          # http://localhost:5173
npm run check         # svelte-check + TypeScript
npm run generate-api   # regenerate src/lib/api/schema.d.ts from the backend's live OpenAPI schema
                        # (needs the backend running on :8000) — re-run after touching
                        # app/schemas.py or the /api/* routes in app/main.py
npm run build          # static SPA output in frontend/build/ (adapter-static)

# Docker (production-shaped; builds the frontend in a Node stage). Secrets (TBR_SECRET_KEY,
# GRIMMORY_BASE_URL, TBR_ADMIN_USERNAME, TBR_PORT) come from .env, gitignored — copy
# .env.example to .env and fill in real values first.
cp .env.example .env
docker compose up -d --build
```

There is no lint/format/typecheck command configured for the backend. The frontend has
`npm run check` (svelte-check + TypeScript) but no separate lint/format step either.

## Architecture

### Backend (`app/`)

- **`main.py`** — the entire route table (one file, no routers/blueprints): every `/api/*` JSON
  route, session-cookie signing/verification, the two auth dependencies (`require_user` — 401 via a
  raised `_LoginRequired` exception + handler; `get_current_user` — returns `None` instead), and
  `spa_fallback`, which serves the built SvelteKit app for everything that isn't `/api/*`,
  `/covers/*`, or `/health`. `spa_fallback` and the `/covers` StaticFiles mount are registered from
  inside `lifespan()`, not as module-level decorators — see that function's comment for why the
  ordering matters (a module-level `/{full_path:path}` route would register *before* `/covers` and
  shadow every cover-image request).
- **`schemas.py`** — Pydantic request/response models for the API, mirroring the dataclasses in
  `models.py`. Secrets (`grimmory_refresh_token`, stored passwords/API keys) are never included in
  any response — settings endpoints only ever expose whether a secret is set
  (`password_set`/`hardcover_api_key_set`/`has_grimmory_session`), never the value.
- **`models.py`** — the entire SQLite schema (`SCHEMA_SQL`) plus every CRUD function. No ORM, no
  migrations framework: `init_db()` runs `CREATE TABLE IF NOT EXISTS` then a hand-rolled sequence of
  `ALTER TABLE ... ADD COLUMN` / `DROP COLUMN` calls guarded by `try/except sqlite3.OperationalError`
  (each guarded by a comment explaining what it's for and when it was added). When adding a column,
  follow that exact pattern — add it to `SCHEMA_SQL` for fresh databases *and* append an `ALTER TABLE`
  step for existing ones.
- **`grimmory_auth.py`** — auth against Grimmory *on behalf of a real person*: user login, the
  refresh-token dance (`get_valid_access_token`, lock-per-user since Grimmory rotates/revokes on
  every use), and the separate admin-privileged actions used for the "spice" content-rating scale.
- **`library_check.py`** — talks to Grimmory as one of two *service* accounts: the read-only sync
  account (periodic catalog cache refresh + fuzzy ownership matching via `rapidfuzz`) and, via
  `sync_user_reading_status`, applies a logged-in user's own Grimmory reading status onto their local
  shelves. Also runs the app's only background task, `run_periodic_sync` (started in `main.py`'s
  lifespan), which loops forever syncing every known user's reading status and then the catalog.
- **`stat_tiles.py` / `reading_calendar.py`** — pure functions over `TBREntryDetail` + raw Grimmory
  session dicts that build the stats/calendar pages' tiles/heatmap/burndown-chart data (the actual
  SVG/chart rendering is client-side now — see `frontend/src/lib/utils/burndown.ts`). No I/O.
- **`metadata.py` / `hardcover.py`** — external book-search providers (Open Library, optional
  Hardcover API) used only when adding a *new* book not already in the Grimmory catalog.
- **`cover_color.py`** — samples an average color from a book's cover image, cached on `books.cover_color`.
- **`dates.py`** — shared instant/date parsing for the "...Z"-suffixed-or-bare-ISO strings Grimmory
  and this app both produce; also the single `longest_consecutive_run` streak algorithm reused by
  both stat modules.

### Frontend (`frontend/`)

SvelteKit, `adapter-static` in SPA mode (`ssr = false` in the root `+layout.ts`, `fallback:
'index.html'` in `vite.config.ts`) — every route is client-rendered, since the production build has
no Node server behind it, only FastAPI serving static files. Route groups: `(auth)/login` is the
only ungated page; `(app)/*` requires a session (its `+layout.ts` redirects to `/login` otherwise);
`admin/*` is intentionally ungated in-app too (see below) but still shares the same chrome. Typed
API access goes through `src/lib/api/client.ts` (`openapi-fetch` + generated `schema.d.ts`) — never
hand-write a `fetch` call to `/api/*`, regenerate the schema instead if a type is missing. Several
pages replicate the old app's CSS-only view toggles (`app.css`'s `#view-spine:checked ~ ...`
sibling-selector pattern) by rendering both variants and letting a hidden radio input's `checked`
state pick one — see `(app)/+layout.svelte` and `CalendarSection.svelte`.

### Three distinct Grimmory account types — don't conflate them

1. **The logged-in user's own credentials** (`grimmory_auth.login`) — used for sign-in and reading
   status/session data scoped to that person. Password is never stored; only the rotating refresh
   token is persisted (`users.grimmory_refresh_token`).
2. **A dedicated read-only sync account** (`library_settings` table, configured at
   `/admin/settings`) — used by `library_check.py` for the periodic full-catalog fetch. Logs in
   fresh on every call.
3. **A separate admin-privileged account** (`grimmory_admin_settings` table) — used only by
   `grimmory_auth.py`'s content-restriction endpoints for the spice scale. Logs in fresh on every call.

### `/admin` and `/admin/settings` have no in-app authorization

Every admin route comment says "gated at the reverse proxy" — access control is expected to happen
at the Nginx Proxy Manager layer (Access List / Basic Auth on that path), not in this codebase.
This carries through to the frontend too: the `admin/` route group's `+layout.ts` never redirects
unauthenticated visitors away, unlike `(app)/*`. Don't add in-app auth there unless asked; it's an
intentional gap, not an oversight.

### Data provenance convention

Several `books`/`tbr_entries` columns are always overwritten from Grimmory on sync and never
user-editable (`page_count`, `rating`, `grimmory_book_id`, `finished_at` when Grimmory has its own
`dateFinished`), while others are local-only and can diverge (`started_at` unless
`started_at_manual` is set). When touching sync logic or the dates form, check the column comment in
`models.py`'s `SCHEMA_SQL` first — it documents which side owns each field and why.

### PWA

`vite-plugin-pwa` generates the service worker/manifest/precache list at build time (see
`vite.config.ts`'s `VitePWA(...)` block) — static assets are precached and content-hashed, so a
stale-JS-after-deploy problem can't happen the way it could with a hand-maintained precache list.
`/api/*` and `/covers/*` are explicitly excluded from precaching/navigation fallback (`NetworkOnly`)
so shelves and covers never go stale or leak between users sharing a device. `index.html` itself is
served with `Cache-Control: no-cache` by `spa_fallback` in `main.py`, since it's the one URL that
never changes across deploys yet must always reference the current content-hashed bundle.

## Testing conventions

Backend tests use FastAPI's `TestClient` against the real `app` object with a per-test SQLite DB
(`monkeypatch.setenv("TBR_DB_PATH", str(tmp_path / "test.db"))`) and a fake `TBR_SECRET_KEY`/
`GRIMMORY_BASE_URL` — see the `client` fixture at the top of `tests/test_api.py`. Grimmory network
calls are monkeypatched at the function level (e.g. `monkeypatch.setattr(grimmory_auth, "login", ...)`)
rather than mocked at the `httpx` layer. Reuse that fixture pattern for new route tests instead of
inventing a new one. There is no automated frontend test suite yet — verify UI changes in a real
browser (e.g. via the `run` skill) against a locally running backend.
