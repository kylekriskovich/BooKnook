/**
 * Which audiobook the admin pair picker sheet (see AdminPairSheet.svelte) is currently pairing —
 * written by an AdminEntry's "Pair to ebook" button just before the sheet's native popover opens
 * (popovertarget alone can't pass which row triggered it). Same singleton-module pattern as
 * $lib/stores/adminMatch.svelte.ts: exactly one sheet instance for the whole /admin page.
 */
class AdminPairTarget {
	audiobookGrimmoryId: number | null = $state(null);
	title: string = $state('');

	set(audiobookGrimmoryId: number, title: string) {
		this.audiobookGrimmoryId = audiobookGrimmoryId;
		this.title = title;
	}
}

export const adminPairTarget = new AdminPairTarget();
