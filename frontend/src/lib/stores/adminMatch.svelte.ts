/**
 * Which needed-entry book the admin match picker sheet (see AdminMatchSheet.svelte) is currently
 * matching — written by an AdminEntry's "Match" button just before the sheet's native popover
 * opens (popovertarget alone can't pass which row triggered it). Same singleton-module pattern as
 * $lib/stores/auth.svelte.ts: exactly one sheet instance for the whole /admin page.
 */
class AdminMatchTarget {
	bookId: number | null = $state(null);
	title: string = $state('');

	set(bookId: number, title: string) {
		this.bookId = bookId;
		this.title = title;
	}
}

export const adminMatchTarget = new AdminMatchTarget();
