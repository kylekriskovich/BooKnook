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

## Physical ownership (separate from `linked_editions`)

Grimmory's own "physical" tag is **catalog-wide, not per-user** — confirmed empirically (marking a
book physical under one Grimmory login showed it as physical when logged in as a different
account). That's the wrong shape for BooKnook's actual users, who don't all share one household
bookshelf (e.g. two users on the same instance who don't live together) — "I own this physically"
must vary per person on the same book. Grimmory's tag is therefore **not used at all** for this;
physical ownership is entirely BooKnook-native.

This splits into two distinct features, only the first of which is being built now:

1. **"I own a physical copy" (building now).** A durable per-(user, book) flag —
   `tbr_entries.owns_physical`, same tier as `rating`/`sort_order`. Set directly by the user, never
   touched by sync. Opens the door to future stats like "% of owned books unread" — not built yet,
   just noted as a reason this is worth having beyond a cosmetic badge.
2. **"I read some of this physically just now" (deferred).** A manual reading-event log — the piece
   that would actually let `started_at`/progress account for a physical-first start or a physical
   stretch mid-read, the way the Dungeon Crawler Carl walkthrough needed. Not started; revisit
   later.

## Open questions (resolve before implementing the relevant phase)

- **Time-spent-by-medium tiles** ("Reading Days" / "Listening Days", "Time Spent Reading/Listening")
  — do these stay split by medium even though progress unifies, or collapse into one figure too?
- **Pace / "Estimated Completion"** — combine reading+listening cadence into one projection, or keep
  it per-medium? Combining is more useful; keeping separate is more honest about conflating two
  different units (pages/day vs. %/day).
- **Does Grimmory's "physical" tag have its own matchable id at all**, or is it a bare flag with no
  catalog identity? Blocks the entire physical phase and the "accepted edge case" reconsideration
  above until answered.

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

- **Phase 0 — no schema change.** Fix `started_at` derivation to include audiobook sessions. Low
  risk, immediately correct, unblocks nothing else but is a clean first PR.
- **Phase 1 — schema generalization.** Add `linked_editions`, backfill from `audiobook_pairings`,
  keep both tables live in parallel for one release (mirrors the existing
  add-then-drop pattern already used for the old `koreader_*` columns in `app/models.py:312-318`).
- **Phase 2 — unify progress/tiles.** Refactor `api_book_detail` + `stat_tiles.build_book_tiles` to
  loop over every linked edition's sessions instead of hardcoded ebook/audiobook branches. Ship the
  unified overall-progress value and burndown chart. Resolve the two open tile questions above as
  part of this phase, not before.
- **Phase 3 — retire the old shape.** Once Phase 2 reads exclusively from `linked_editions`, drop
  `audiobook_pairings` and `tbr_entries.audiobook_progress_percent` in a follow-up release.
- **Phase 4 — physical support.** Blocked on the Grimmory physical-tag question above. Also the
  point to revisit the physical-only-forever edge case from Decision 1.

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
