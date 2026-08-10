<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { api, unwrap } from '$lib/api/client';
	import type { TBREntry } from '$lib/utils/entries';
	import BookHeader from './BookHeader.svelte';

	let { entry }: { entry: TBREntry } = $props();

	let removing = $state(false);

	async function remove() {
		removing = true;
		try {
			unwrap(await api.POST('/api/tbr/{entry_id}/remove', { params: { path: { entry_id: entry.id } } }));
			// Every page's load() re-fetches from the API rather than this component reaching into
			// a parent's local list — one mutation pattern reused everywhere (see AddSheet.svelte),
			// simpler than threading an onRemoved callback through ShelfRow/shelf pages/home.
			await invalidateAll();
		} finally {
			removing = false;
		}
	}
</script>

<div id="confirm-{entry.id}" popover class="book-modal">
	<BookHeader {entry} />
	<div class="confirm-actions">
		<button type="button" popovertarget="confirm-{entry.id}" popovertargetaction="hide" class="btn btn-ghost">Keep</button>
		<button type="button" class="btn btn-danger" disabled={removing} onclick={remove}>Remove from Shelf</button>
	</div>
</div>
