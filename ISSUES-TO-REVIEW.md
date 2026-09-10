# Issues to Review

Running scratch list of things noticed while exploring the codebase — not deep-dived, not confirmed
as real problems. Just flagged here so they don't get surfaced in conversation and then lost. Not
committed to git; local working notes only.

Each entry: one line, a pointer to where it came from, nothing more. Triage later by moving it to
Confirmed / Not an issue / Won't fix with a one-line reason.

See also `DESIGN-multi-edition-refactor.md` for the larger multi-edition (ebook/audiobook/physical)
data-model rework — several items below were absorbed into or resolved by that discussion rather
than staying standalone issues.

GitHub issues (github.com/kylekriskovich/BooKnook/issues) track findings formally, including a
prior automated code-review pass — 15 open, 0 closed as of this review. This file only duplicates
one when it sharpens or corrects something already listed here; the rest stay GitHub-only rather
than being copied over.

## Open

- [ ] Pace/"Estimated Completion" averages progress over the *entire* reading span so far — a recent
  binge session after weeks idle skews the estimate optimistic. (`app/stat_tiles.py:164-186`) — also
  now one of the open questions in `DESIGN-multi-edition-refactor.md` (combine reading+listening
  cadence, or keep per-medium).
- [ ] "Total/Avg pages read" on stats + calendar are prorated across a book's started→finished span,
  not measured — can misattribute pages across a month/year boundary. (`app/stat_tiles.py:220-244`,
  `app/reading_calendar.py:141-157`)
- [ ] Want-to-Read shelf diff-and-sync has no cross-request locking beyond the per-user refresh-token
  lock — concurrent syncs for the same user could race on shelf membership. (`app/library_check.py:930-947`)
  Confirmed and detailed via GitHub #12 (suggests reusing the existing per-user refresh-token lock
  around Passes 3/4); GitHub #13 documents the same missing-lock class producing duplicate local
  books for ISBN-less shelf pull-ins, since `create_book` only dedups on ISBN.
- [ ] Reading-date/streak calculations truncate session timestamps to a UTC calendar date, but "today"
  for the client is the browser's local date — possible off-by-one-day for non-UTC users in streaks/
  calendar cells. (`app/dates.py:35-37` vs `app/main.py:311-328`)
- [ ] `books.title` / `author` / `isbn` are captured once when a book is first added and never
  resynced from Grimmory afterward — a later correction in Grimmory won't propagate.
  (`app/models.py` `create_book`)
- [ ] Cover images are downloaded once and never re-fetched once a local copy exists — a changed
  Grimmory cover won't update. (`app/library_check.py:66-67`, `_has_local_cover` gate)
- [ ] All of `/api/admin/*` is ungated in code by design (relies on reverse-proxy access control) —
  worth confirming the actual deployment's reverse proxy is configured that way, since nothing in the
  app itself would catch a misconfiguration. (`app/main.py` module docstring)
- [ ] Goals only support the "year" timeframe — month/week are unimplemented. (`app/models.py:207-209`)
- [ ] No re-read history — re-finishing a book overwrites the previous `finished_at`/`rating` with
  nothing retained from the prior read.
- [ ] There is no endpoint to manually set a shelf status (wanted/reading/finished) at all — status
  only ever moves via the Grimmory sync. Harmless today, but means a book that can never get a
  Grimmory match (see the physical-only-forever edge case in `DESIGN-multi-edition-refactor.md`,
  Decision 1) has no way out of "wanted," ever, not even by hand.
- [ ] The paired-audiobook lookup in book detail inverts `audiobook_pairings` with a plain dict
  comprehension (`audiobook_by_ebook_id = {v: k for k, v in pairings.items()}`, `app/main.py:686`) —
  if two different audiobook editions were ever paired to the same ebook, one would silently vanish
  with no error. Only matters if "at most one linked audiobook per book" isn't actually guaranteed
  elsewhere — worth deciding as part of the `linked_editions` schema in
  `DESIGN-multi-edition-refactor.md`.

## Confirmed

- [x] `started_at` is frequently a sync-time guess, not a real start date, and specifically never
  considered a paired audiobook's own sessions when deriving/correcting it.
  (`app/library_check.py:575-580`) — confirmed as a real bug via a worked example (audiobook-first
  reading). **Fixed** — Phase 0 of `DESIGN-multi-edition-refactor.md`, `app/main.py:716-730`, on
  `feature/linked-book-refactor`.
- [x] Audiobook support is disabled (`AUDIOBOOKS_ENABLED = False`) with the columns/branches/pairing
  logic still in place. (`app/library_check.py:99`) — not simply dead code to delete: the pairing
  feature is a real, actively-used, independent feature (Listening tab, admin pairing UI). Superseded
  by the full multi-edition rework in `DESIGN-multi-edition-refactor.md` rather than a standalone
  cleanup.
- [x] "Owned" / In-Library badge ignores an admin's manual catalog-match pin entirely at two call
  sites — worse than the originally-flagged "fuzzy matching can misfire on similar titles": a real
  manual override is silently ignored. Confirmed via GitHub #10 (`sync_user_reading_status` Pass 1,
  `app/library_check.py:766`, calls `find_catalog_match` instead of `resolve_catalog_match`) and
  #11 (Home/Shelf pages, `app/main.py:270`, same bug). `GET /api/admin` already does this correctly
  (`app/main.py:1131`) — both fixes are a one-line swap to the resolver already used there.
- [x] `_dedupe_by_grimmory_id`'s code comment (`app/library_check.py:731-736`) attributes the need
  for dedup to "Grimmory's `/books` response returning the same id twice in one call, see issue #22"
  — but the actual GitHub #22 describes a different mechanism entirely (below). The comment may
  mischaracterize what #22 actually found; worth a closer look before trusting it at face value.
- [x] Confirmed via GitHub #22: duplicate local `books`/`tbr_entries` rows pile up over time as
  Grimmory metadata drifts between syncs — one production book had **11 duplicate rows**. Root
  cause: Pass 1 always re-derives a match via fuzzy matching even for a book with an already-known
  `grimmory_book_id`, instead of checking the id directly first. Same call site as #10 above —
  likely fixable together.

## Not an issue / won't fix

- [x] Entry status only ever moves forward (wanted→reading→finished), and any linked edition
  (ebook or paired audiobook) can advance it, including straight to "finished." Originally flagged as
  a possible contradiction (an audiobook alone finishing a book). Reasoning it through against a real
  mixed-medium read (started via audiobook while driving, finished via ebook/physical days later)
  showed this is correct, not a bug — no medium is less legitimate evidence that a book is actually
  being read. See Decision 3 in `DESIGN-multi-edition-refactor.md`.
