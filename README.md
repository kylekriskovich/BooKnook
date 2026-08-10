# Book Knook

> **⚠️ Early Development** — this project is actively changing. Expect breaking changes, rough
> edges, and incomplete documentation until this notice comes down.

A shared to-be-read / reading tracker that layers on top of a self-hosted
[Grimmory](https://github.com/grimmory-tools/grimmory) library (a BookLore fork). There's no
separate account system or book catalog of its own — every user signs in with their real Grimmory
username/password, and "Currently Reading" / "Finished" status is derived automatically from each
user's Grimmory reading sessions rather than set by hand.

## Features

- Shared household TBR shelves — Currently Reading / To Be Read / Finished — with a spine or cover
  view
- Add books via Open Library or an optional [Hardcover.app](https://hardcover.app) search, or
  straight from what's already in your Grimmory library
- Per-book reading stats (pages/day, best streak, a burndown chart) pulled from Grimmory reading
  sessions
- A reading-activity calendar across everyone's books, month by month
- Yearly reading goals
- Installable as a PWA
- Optional library cross-check — flags which requested books are already owned in Grimmory
- Optional "spice" content-rating scale, synced to Grimmory's own content restrictions

## Stack

- **Backend:** FastAPI (Python), SQLite
- **Frontend:** SvelteKit (TypeScript), installable PWA via `vite-plugin-pwa`
- Both ship in **one Docker image** — FastAPI serves the built SvelteKit app directly, so this
  stays a single self-hostable container rather than a multi-service deployment.

## Getting started

See [SETUP.md](SETUP.md) for installation (Docker Compose, recommended) and local development
instructions.

## License

[GPLv3](LICENSE).
