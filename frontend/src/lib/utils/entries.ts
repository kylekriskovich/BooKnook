import type { components } from '$lib/api/schema';

export type TBREntry = components['schemas']['TBREntryOut'];

// "reading"/"finished" link straight to the book detail page; "wanted" opens the keep/remove
// confirm popover instead (see BookModal.svelte).
export function linksToDetail(entry: TBREntry): boolean {
	return entry.status === 'reading' || entry.status === 'finished';
}
