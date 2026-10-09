/**
 * Which row a picker sheet (AdminPickSheet.svelte) is currently targeting - written by a row's
 * action button just before the sheet's native popover opens (popovertarget alone can't pass
 * which row triggered it). One instance per sheet.
 */
export function createPickerTarget() {
	let id = $state<number | null>(null);
	let title = $state('');
	return {
		get id() {
			return id;
		},
		get title() {
			return title;
		},
		set(newId: number, newTitle: string) {
			id = newId;
			title = newTitle;
		}
	};
}
