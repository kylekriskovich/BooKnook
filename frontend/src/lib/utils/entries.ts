import type { components } from '$lib/api/schema';

export type TBREntry = components['schemas']['TBREntryOut'];

// Mirrors the "reading"/"finished" tuple checks scattered through app/templates/*.html (e.g.
// _shelf_row.html, _shelf_books.html) — those two statuses link straight to the book detail page;
// "wanted" opens the keep/remove confirm popover instead (see BookModal.svelte).
export function linksToDetail(entry: TBREntry): boolean {
	return entry.status === 'reading' || entry.status === 'finished';
}
