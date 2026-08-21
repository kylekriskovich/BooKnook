#!/usr/bin/env python3
"""One-off cleanup for GitHub issue #22: merges local `books` rows that share the same
grimmory_book_id (created by the sync-matching bug fixed in PR #23, since deployed) back into a
single canonical row per Grimmory book, dropping the resulting duplicate tbr_entries. For each
user's entries in a cluster, keeps whichever one already points at the canonical book, else the
earliest-added one (reassigned onto canonical) — status/started_at/rating all get re-derived from
Grimmory on the next sync regardless, so no field-level merging is needed.

Dry run by default — prints what it would do without touching the database. Pass --apply to commit.

Usage:
    python scripts/dedupe_duplicate_books.py /path/to/tbr.db          # dry run
    python scripts/dedupe_duplicate_books.py /path/to/tbr.db --apply  # commit
"""

from __future__ import annotations

import argparse
import sqlite3


def find_duplicate_clusters(conn: sqlite3.Connection) -> dict[int, list[sqlite3.Row]]:
    rows = conn.execute(
        "SELECT id, grimmory_book_id, isbn, title, cover_url FROM books WHERE grimmory_book_id IS NOT NULL"
    ).fetchall()
    by_grimmory_id: dict[int, list[sqlite3.Row]] = {}
    for row in rows:
        by_grimmory_id.setdefault(row["grimmory_book_id"], []).append(row)
    return {gid: book_rows for gid, book_rows in by_grimmory_id.items() if len(book_rows) > 1}


def choose_canonical(book_rows: list[sqlite3.Row], entry_counts: dict[int, int]) -> sqlite3.Row:
    # Prefer whichever row is already referenced by the most tbr_entries (minimizes reassignment
    # and preserves whatever the user was already seeing), then isbn set, then cover_url set, then
    # lowest id — isbn/cover_url are set once at creation and never re-synced from Grimmory, unlike
    # page_count/rating/grimmory_id/format, so picking the "wrong" row loses them for good.
    def sort_key(row: sqlite3.Row) -> tuple[int, int, int, int]:
        return (
            -entry_counts.get(row["id"], 0),
            0 if row["isbn"] else 1,
            0 if row["cover_url"] else 1,
            row["id"],
        )

    return min(book_rows, key=sort_key)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("db_path")
    parser.add_argument("--apply", action="store_true", help="Actually commit changes (default: dry run)")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row

    clusters = find_duplicate_clusters(conn)
    if not clusters:
        print("No duplicate grimmory_book_id clusters found.")
        conn.close()
        return

    total_entries_dropped = 0
    total_books_dropped = 0

    for grimmory_id, book_rows in sorted(clusters.items()):
        all_book_ids = [r["id"] for r in book_rows]
        placeholders = ",".join("?" * len(all_book_ids))
        entries = conn.execute(
            f"SELECT * FROM tbr_entries WHERE book_id IN ({placeholders}) ORDER BY user_id, added_at",
            all_book_ids,
        ).fetchall()
        entry_counts: dict[int, int] = {}
        for entry in entries:
            entry_counts[entry["book_id"]] = entry_counts.get(entry["book_id"], 0) + 1

        canonical = choose_canonical(book_rows, entry_counts)
        duplicate_ids = [r["id"] for r in book_rows if r["id"] != canonical["id"]]
        print(f"\ngrimmory_book_id={grimmory_id} ({canonical['title']!r}): "
              f"keeping book id={canonical['id']}, removing books {duplicate_ids}")

        by_user: dict[int, list[sqlite3.Row]] = {}
        for entry in entries:
            by_user.setdefault(entry["user_id"], []).append(entry)

        for user_id, user_entries in by_user.items():
            canonical_entry = next((e for e in user_entries if e["book_id"] == canonical["id"]), None)
            if canonical_entry is not None:
                keeper = canonical_entry
                others = [e for e in user_entries if e["id"] != keeper["id"]]
            else:
                keeper = min(user_entries, key=lambda e: e["added_at"])
                others = [e for e in user_entries if e["id"] != keeper["id"]]

            print(f"  user_id={user_id}: keep entry id={keeper['id']} "
                  f"(book_id {keeper['book_id']} -> {canonical['id']}), "
                  f"drop entries {[e['id'] for e in others]}")

            if args.apply:
                if keeper["book_id"] != canonical["id"]:
                    conn.execute(
                        "UPDATE tbr_entries SET book_id = ? WHERE id = ?", (canonical["id"], keeper["id"])
                    )
                for loser in others:
                    conn.execute("DELETE FROM tbr_entries WHERE id = ?", (loser["id"],))
            total_entries_dropped += len(others)

        if args.apply:
            for dup_id in duplicate_ids:
                conn.execute("DELETE FROM books WHERE id = ?", (dup_id,))
        total_books_dropped += len(duplicate_ids)

    print(f"\n{'APPLIED' if args.apply else 'DRY RUN'}: "
          f"{total_entries_dropped} duplicate tbr_entries, {total_books_dropped} duplicate books "
          f"rows across {len(clusters)} clusters.")

    if args.apply:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok" or fk_violations:
            conn.rollback()
            print(f"ABORTED — post-change check failed (integrity={integrity!r}, "
                  f"fk_violations={fk_violations}). No changes committed.")
        else:
            conn.commit()
            print("Committed. integrity_check and foreign_key_check both clean.")
    else:
        print("Dry run only — re-run with --apply to commit.")

    conn.close()


if __name__ == "__main__":
    main()
