# BooKnook frontend

SvelteKit (adapter-static, SPA mode) frontend for BooKnook, talking to the FastAPI backend in
`../app/` over `/api/*`. See the root `CLAUDE.md` for the overall migration plan — this frontend
is being built out page-by-page while the old Jinja2/htmx routes keep running unchanged in `app/`.

## Developing

Needs the FastAPI backend running on `:8000` (`uvicorn app.main:app --reload` from the repo root) —
the dev server proxies `/api`, `/covers`, and `/health` to it (see `vite.config.ts`), so cookies
and requests behave the same as they will in production, where FastAPI serves this app's build
output directly and everything is same-origin.

```sh
npm install
npm run dev          # http://localhost:5173, proxies to a backend on :8000
npm run check         # svelte-check + TypeScript
npm run generate-api   # regenerate src/lib/api/schema.d.ts from the backend's live OpenAPI schema
```

`generate-api` requires the backend running on `:8000` — it hits `http://localhost:8000/openapi.json`.
Re-run it whenever `app/schemas.py` or the `/api/*` routes in `app/main.py` change.

## Building

```sh
npm run build    # static output in build/ (adapter-static, fallback: 'index.html')
npm run preview   # serve the production build locally
```
