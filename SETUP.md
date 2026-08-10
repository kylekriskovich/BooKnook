# Setup

## Prerequisites

- A running [Grimmory](https://github.com/grimmory-tools/grimmory) (or other BookLore-family)
  instance — BooKnook has no book catalog or user accounts of its own; it authenticates against
  Grimmory and pulls reading status from there.
- Docker + Docker Compose (recommended), or Python 3.12+ and Node.js 22+ for a manual/dev setup.

## Docker (recommended)

`docker-compose.yml`'s `build` points straight at this repo's `latest` tag on GitHub, not a local
checkout — you only need `docker-compose.yml` and your own `.env` on disk, not a full clone (though
cloning is the easiest way to grab both).

1. Get `docker-compose.yml` and `.env.example` — either clone the repo:

   ```bash
   git clone https://github.com/kylekriskovich/BooKnook.git
   cd BooKnook
   ```

   or just download those two files directly if you don't want a local checkout at all.

2. Copy the env template and fill it in:

   ```bash
   cp .env.example .env
   ```

   | Variable | Required | Description |
   |---|---|---|
   | `GRIMMORY_BASE_URL` | Yes | Base URL of your Grimmory instance, e.g. `https://library.example.com` |
   | `TBR_SECRET_KEY` | Yes | Random key used to sign session cookies. Generate one: `openssl rand -hex 32` |
   | `TBR_ADMIN_USERNAME` | Yes | Grimmory username of the app admin — their account gets an "Admin" shortcut in the nav. UI convenience only; it does **not** gate the `/admin` pages themselves (see below). |
   | `TBR_PORT` | No (default `8011`) | Host port to publish the app on. |

3. Build and start:

   ```bash
   docker compose up -d --build
   ```

4. Visit `http://localhost:<TBR_PORT>` and log in with your Grimmory username/password.

To update later, just rebuild — no need to re-clone or `git pull` first:

```bash
docker compose up -d --build
```

### Releasing a new version (maintainers)

Releases are plain semver git tags (e.g. `v0.1.0-alpha`), plus a `latest` tag that's deliberately
moved to point at whichever release is current — that's what `docker-compose.yml`'s build context
tracks (`...git#latest`), rather than `main` directly, so pushing to `main` alone never changes
what a deployed instance builds.

```bash
git tag v0.1.0-alpha
git push origin v0.1.0-alpha

git tag -f latest v0.1.0-alpha   # -f: moves the tag if it already points elsewhere
git push origin latest --force
```

Anyone who then runs `docker compose up -d --build` picks up that release.

### Securing `/admin`

`/admin` and `/admin/settings` have **no built-in authentication** — they're meant to be gated at
your reverse proxy (an Access List rule, Basic Auth, etc. — e.g. in Nginx Proxy Manager, Caddy, or
Traefik) before the app is reachable beyond your own network. Don't expose `/admin*` publicly
without adding that yourself.

From `/admin/settings` you can optionally configure:

- **Library cross-check** — a read-only Grimmory service account BooKnook uses to periodically
  sync the catalog and flag which requested books you already own.
- **Grimmory admin credentials** — a Grimmory account with admin rights, used only for the
  household "spice" content-rating scale.
- **Hardcover API key** — swaps the default Open Library search for
  [Hardcover.app](https://hardcover.app)'s, if you have an API key.

## Local development (without Docker)

**Backend:**

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
export TBR_SECRET_KEY=dev-secret GRIMMORY_BASE_URL=https://your-grimmory-instance
uvicorn app.main:app --reload
```

**Frontend** (separate terminal — proxies `/api`, `/covers`, `/health` to the backend above):

```bash
cd frontend
npm install
npm run dev
```

Then visit `http://localhost:5173`.

**Tests:** `pytest` from the repo root (backend) and `npm run check` from `frontend/`
(TypeScript/svelte-check). See `CLAUDE.md` for more on the codebase's architecture and conventions.
