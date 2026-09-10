# Multi-edition book model — design & migration plan

**Status:** design settled through discussion (see Decisions below); no implementation started yet.
Not committed to git — local planning notes, update freely as thinking evolves.

## Why

BooKnook currently hardcodes "ebook" as the only real edition a book can have, with audiobook
bolted on as a single special-cased table (`audiobook_pairings`) + a single special-cased column
(`tbr_entries.audiobook_progress_percent`). That doesn't generalize to a third format (physical,
planned), and the shared `status`/`started_at`/`finished_at` fields on `tbr_entries` don't record
*which* edition actually produced a given value, which was already causing real inaccuracies (see
`ISSUES-TO-REVIEW.md`). This doc works out what the model should actually be, grounded against a
real reading history (reading *Dungeon Crawler Carl* across audiobook → ebook → physical), before
touching any code.

## Confirmed decisions

1. **The ebook is a required anchor.** No status or date automation happens for a book — single- or
   multi-format — until it has a matched ebook edition (a Grimmory book with a stable id and a
   reading-session log). Audiobook and physical are enrichments layered on top of that anchor, never
   a substitute for it. This isn't audiobook-specific — it's the one invariant that makes automation
   possible at all, since the ebook is the only format guaranteed to be both matchable and
   session-capable.
   - **Accepted edge case:** a book that will never have an ebook edition (physical-only, never
     digitized) can never leave "wanted" under this rule, and there's currently no manual override
     to promote it. Accepted for now, since BooKnook's whole premise is layering on a digital
     library. **Revisit once physical-edition support actually ships** — at that point "can a
     physical-only copy anchor a book" becomes a live question again, not a hypothetical.

2. **`started_at` = earliest tracked session across *every* linked edition**, capped by a manual
   edit that then wins permanently (this override behavior already exists via
   `started_at_manual` — no change needed there). Bug to fix: `first_meaningful_session_date`
   (`app/main.py:714-718`) currently only ever looks at the ebook's own sessions, never a paired
   audiobook's, so it already gets this wrong for an audiobook-first read.
   - Physical-first starts can never be auto-derived (no Grimmory signal exists for physical
     reading at all) — manual correction is the *only* possible answer there, not a stopgap.

3. **Status keeps advancing from any matched, linked edition** — no medium is less legitimate
   evidence that a book is being read. (Already correct in `_apply_status`/Pass 2b; keep as-is.)

4. **`finished_at` is best-effort, and manual correction is the primary answer for a cross-medium
   finish, not a fallback.** A book finished via an untracked medium (physical) will often have no
   correct auto-derived date at all — there's no algorithm fix for that, only a UI that makes
   correcting it easy. (Also note the asymmetry: `finished_at` has a real upstream Grimmory field a
   disciplined user can set themselves; `started_at` has no equivalent field at all.)

5. **Introduce one unified "overall progress" value** = `max()` of the latest tracked percentage
   across all linked editions, replacing the current two separate ebook/audiobook progress
   percentages in the UI. The burndown chart should unify the same way — one progress-over-time
   line fed by sessions from whichever edition was used each day, not two parallel charts that each
   go stale while the other medium is in use.
   - Progress is permanently invisible during a physical-reading stretch and will visibly "jump"
     once a tracked medium resumes — that's an honest reflection of a real gap, not a bug to solve.

6. **Time-spent-by-medium tiles stay split, even though overall progress unifies.** "Reading Days" /
   "Listening Days" and "Time Spent Reading" / "Time Spent Listening" remain separate — that's real,
   different information about *how* you engaged with the book, not a progress claim, so collapsing
   it into overall progress would lose something worth keeping.

7. **Physical reading sessions are logged manually, mimicking Grimmory's own session shape** — a
   form capturing start datetime, end datetime (duration derived), start page, end page (progress/
   delta derived) — rather than inventing a different data shape. Once converted to a
   Grimmory-shaped session dict, it flows through the *existing* pipeline
   (`_has_meaningful_progress`, `latest_progress`, `get_reading_dates`, `burndown_points`) with zero
   new aggregation logic.

8. **Physical sessions merge into the Reading tile bucket, not a third tab.** Unlike audiobook
   (a genuinely different activity, hence its own Listening bucket), physical page-turning is the
   same activity as ebook reading, just untracked by Grimmory — "Reading Days"/"Time Spent Reading"
   should include physical sessions directly. See "Session source generalization" below for how
   this and Decision 6 combine.

9. **Page counts are edition-specific, and the fix is to never let it matter past entry time.**
   Grimmory's `books.page_count` reflects whichever digital file is cataloged — a physical printing
   can have a genuinely different page count (different publisher/trim/edition). Grimmory's own
   sessions never store page numbers at all, only percentages (`startProgress`/`endProgress`) — so
   a manual physical entry's `start_page`/`end_page` get converted to a percentage *once*, at entry
   time, using a separate `tbr_entries.physical_page_count` (not `books.page_count`). After that
   conversion the stored session is pure percentage data, identical in shape to every other source,
   and nothing downstream ever needs to reconcile two different page counts again.
   - `physical_page_count` is prompted (optional) when `owns_physical` is switched on, pre-filled
     into future entry forms, and independently editable later — a mistyped count or a different
     printing shouldn't require re-entering every session already logged.

## Session source generalization

Two different groupings apply once there are 3 possible session sources (ebook, audiobook, manual
physical), not the 2 the Phase 2 code currently hardcodes:

- **Progress/burndown/started_at are fully format-agnostic** — every source's sessions simply
  flatten into one list, no bucketing. Already true today for 2 sources
  (`sessions + audiobook_sessions` in `main.py`); adding a 3rd is mechanical once physical sessions
  are Grimmory-shaped dicts.
- **Tiles group by bucket, not by source** — "reading" (ebook + physical) vs "listening"
  (audiobook), per Decision 8. Not a fully open-ended per-format system; only two buckets exist.

Suggested shape to replace the current `sessions`/`audiobook_sessions` pair of variables:

```python
@dataclass
class SourceSessions:
    bucket: str  # "reading" | "listening"
    sessions: list[dict]
    fallback_percent: float | None = None  # e.g. audiobook's synced percentage fallback
```
Progress/burndown/started_at flatten every source regardless of bucket; tiles group by bucket
before calling `build_book_tiles`.

## Physical ownership (separate from `linked_editions`)

Grimmory's own "physical" tag is **catalog-wide, not per-user** — confirmed empirically (marking a
book physical under one Grimmory login showed it as physical when logged in as a different
account). That's the wrong shape for BooKnook's actual users, who don't all share one household
bookshelf (e.g. two users on the same instance who don't live together) — "I own this physically"
must vary per person on the same book. Grimmory's tag is therefore **not used at all** for this;
physical ownership is entirely BooKnook-native.

This splits into two distinct features, only the first of which is being built now:

1. **"I own a physical copy" — DONE.** `tbr_entries.owns_physical`, same tier as `rating`/
   `sort_order`. Set directly by the user (a modern switch, not a checkbox), never touched by sync.
   Shown as a badge on `BookHeader` and toggleable from both the book detail page
   (`PhysicalOwnershipSection.svelte`) and the wanted-shelf modal (`BookModal.svelte`) — so a book
   can be marked physical before it's ever matched to an ebook. Opens the door to future stats like
   "% of owned books unread" — not built yet, just noted as a reason this is worth having beyond a
   cosmetic badge.
2. **"I read some of this physically just now" — DONE, backend and frontend.**
   `physical_reading_sessions` (`app/models.py`), `tbr_entries.physical_page_count`, the
   `stat_tiles.physical_session_to_grimmory_shape` adapter, and endpoints (`GET`/`POST
   /api/tbr/{id}/physical-sessions`, `.../physical-sessions/{id}`, `.../physical-sessions/{id}/
   remove`, `POST .../physical-page-count`) are all shipped and wired into `api_book_detail`'s
   started_at/progress/burndown/tiles pipeline exactly per Decisions 7-9. Frontend:
   `PhysicalReadingSessionsSection.svelte` (list + inline add/edit form) lives in the same
   edit-mode block as `PhysicalOwnershipSection`/`ReadingDatesSection`, only rendered when
   `entry.owns_physical`; its own page-count field lives inside `PhysicalOwnershipSection`,
   auto-saving on blur. The "log a session" trigger is a "+" button in the page's top header, next
   to the edit pencil, gated on `editingDates && entry.owns_physical` - `adding` is a bindable prop
   so that external button can open the form. Verified end-to-end via real browser interaction
   (not just API calls) against real production data: create, edit, delete, and list-display all
   confirmed, plus the full effect on tiles/burndown ("Time Spent Reading" gained exactly the
   logged session's duration, "Best Session" correctly re-expressed the physical page range as a
   percentage of the *ebook's* page count). Caught and fixed a real bug along the way: the page's
   `editingDates` reset effect depended on `entry.id` directly, but `entry` is a fresh object on
   every `invalidateAll()` reload (even for the same book), so it was resetting edit mode after
   every single save - fixed by isolating `entryId = $derived(entry.id)` as its own primitive
   dependency.

## Open questions (resolve before implementing the relevant phase)

- **Pace / "Estimated Completion"** — combine reading+listening cadence into one projection, or keep
  it per-medium? Deprioritized — acceptable for this tile to be temporarily inaccurate/left as-is
  through the Phase 2 rework; not a blocker for anything else.
- ~~Does Grimmory's "physical" tag have its own matchable id at all?~~ Moot — resolved by testing:
  the tag is catalog-wide, not per-user (confirmed empirically), so it's unusable for BooKnook's
  purposes regardless of its exact shape. Physical ownership is entirely BooKnook-native instead
  (see "Physical ownership" section above) and doesn't touch `linked_editions` or Grimmory at all.
- ~~Manual physical session table shape~~ Settled and built: `physical_reading_sessions(id,
  entry_id, start_time, end_time, start_page, end_page)`, entry-id-keyed (per-user), as sketched.
- ~~Entry/edit form UI~~ Built - see the "I read some of this physically just now" entry above.

## Schema — `linked_editions` (settled)

**Keyed by catalog-level Grimmory id, not by local `book_id`.** Decided by checking
`api_admin_pair_audiobook` (`app/main.py:1282-1305`): pairing today operates entirely on catalog
ids via `get_library_catalog()`, with no reference to any local `books` row at all — an admin can
pair an audiobook to an ebook nobody has added to a shelf yet. `book_id`-keying would make that
impossible outright, since most of the catalog has no local book row. The one theoretical downside
(an edition link going stale if the anchor ebook is later re-matched to a different Grimmory id) is
an existing, unchanged exposure — `audiobook_pairings` has the identical risk today — not something
`book_id`-keying would actually fix, so it doesn't change the call.

```
linked_editions(
    edition_grimmory_id INTEGER PRIMARY KEY,  -- this edition's own id — same uniqueness guarantee
                                               -- audiobook_pairings has today (one ebook per edition)
    ebook_grimmory_id   INTEGER NOT NULL,     -- the anchor this edition is linked to
    format              TEXT NOT NULL         -- 'AUDIOBOOK' | 'PHYSICAL' | ...
)
```
plus `UNIQUE(ebook_grimmory_id, format)` — at most one linked edition of a given format per ebook,
enforced by the schema rather than silently dropped by a dict comprehension at read time (see the
`audiobook_by_ebook_id` collapse risk logged in `ISSUES-TO-REVIEW.md`).

Per-edition cached progress (if still needed rather than always live-fetched) would live keyed off
`edition_grimmory_id` rather than as a bolt-on column per format.

## Phased rollout

Large refactor — breaking it down so each phase ships and is verifiable on its own:

- **Phase 0 — no schema change. DONE.** Fixed `started_at` derivation to consider every linked
  edition's sessions, not just the ebook's (`app/main.py:716-730`) — takes the earliest across all,
  matching Decision 2. Regression test:
  `test_api_book_detail_derives_started_at_from_earliest_of_any_linked_edition`.
- **Phase 1 — schema generalization. DONE.** Added `linked_editions` (`app/models.py`), one-time
  backfill from `audiobook_pairings` in `init_db()`, and dual-write from
  `api_admin_pair_audiobook` (`app/main.py`) so the new table stays current for the whole
  transition rather than only reflecting a migration-time snapshot. Nothing yet reads from
  `linked_editions` - `audiobook_pairings` remains the only table anything queries, unchanged.
  Tests: `test_api_admin_pair_audiobook_dual_writes_linked_editions`,
  `test_init_db_backfills_linked_editions_from_audiobook_pairings`.
- **Phase 2 — unify progress/tiles. DONE.** `api_book_detail` now computes one unified
  `progress_percent`/`estimated_page` (`_unified_progress_and_estimated_page`, `main.py`) via
  `stat_tiles.unified_latest_progress`, and one merged `burndown` (`sessions + audiobook_sessions`
  concatenated through the existing `burndown_points`). `tiles`/`audiobook_tiles` stay split per
  Decision 6; `_days_to_complete_tile` now only appends to the primary list, fixing a pre-existing
  duplication onto the Listening tab. Frontend: `ProgressSection.svelte` replaces the old
  per-tab `BookStatsPanel.svelte` (removed), rendered once above the Reading/Listening tabs. Verified
  against real production data (not just synthetic fixtures) via an isolated Docker container running
  a clone of the live data volume. Tests: `unified_latest_progress` unit tests,
  `test_api_book_detail_burndown_merges_sessions_across_linked_editions`,
  `test_days_to_complete_is_not_duplicated_onto_the_listening_tab`, among others in
  `tests/test_api.py`/`tests/test_stat_tiles.py`.
  **Gap closed by the manual-physical-sessions work below**: `api_book_detail`'s paired-audiobook
  lookup originally still called `get_audiobook_pairings(db_connection)` directly rather than
  `linked_editions` - now fixed, see that entry.
- **Phase 3 — retire the old shape.** `api_book_detail`'s lookup now reads `linked_editions`
  exclusively (fixed as part of the manual-physical-sessions work below) - `audiobook_pairings` is
  otherwise still read by the fuzzy-match-based paths (`_tbr_entries_for_user`, `GET /api/admin`,
  `api_admin_pair_audiobook`'s dual-write itself), so those still need switching over before
  `audiobook_pairings`/`tbr_entries.audiobook_progress_percent` can actually be dropped. Not done.
- **Physical ownership flag — DONE, out of band from these phases.** `tbr_entries.owns_physical`
  shipped independently (commit `57afede`) since it never touches `linked_editions` or Grimmory at
  all.
- **Manual physical reading sessions — backend DONE, frontend not started.** Also out of band from
  `linked_editions` (Grimmory involvement is zero). Along the way this also closed the Phase 2 gap
  above: `api_book_detail`'s audiobook lookup now reads `linked_editions` via
  `get_linked_editions_for_ebook` instead of `get_audiobook_pairings`'s reverse dict. What's left:
  the frontend entry-form UI (see "Open questions"), and revisiting the physical-only-forever edge
  case from Decision 1 once that UI exists — a book with manually-logged physical sessions but no
  ebook match yet is still stuck, since Decision 1's anchor requirement is unchanged.

## Migration notes

- Follow the existing hand-rolled migration style in `app/models.py:init_db` (`CREATE TABLE IF NOT
  EXISTS` + guarded `ALTER TABLE` calls) — no migrations framework in this codebase, don't introduce
  one for this.
- Backfilling `linked_editions` from `audiobook_pairings` is a straight row-for-row copy if the
  catalog-id-keyed schema above is what we land on; no local-book resolution needed at migration
  time either way, since the table wouldn't be `book_id`-keyed.
- Retire old columns/tables only after the new code path is confirmed to no longer read them —
  same two-step (deprecate, then drop next release) already proven safe in this codebase for the
  KOReader column removal.

## Changelog

- _(none yet — append an entry here as decisions change or phases land)_
