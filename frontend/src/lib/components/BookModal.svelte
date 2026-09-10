<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { api, unwrap } from '$lib/api/client';
	import type { TBREntry } from '$lib/utils/entries';
	import BookHeader from './BookHeader.svelte';
	import PhysicalOwnershipSection from './PhysicalOwnershipSection.svelte';

	let { entry }: { entry: TBREntry } = $props();

	let editing = $state(false);
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

<div
	id="confirm-{entry.id}"
	popover
	class="book-modal"
	ontoggle={(event) => {
		if (event.newState === 'closed') editing = false;
	}}
>
	<div class="book-modal-header">
		<button
			type="button"
			popovertarget="confirm-{entry.id}"
			popovertargetaction="hide"
			class="iconbtn"
			aria-label="Close"
		>
			<svg viewBox="0 -960 960 960" fill="currentColor" aria-hidden="true">
				<path
					d="M480-424 284-228q-11 11-28 11t-28-11q-11-11-11-28t11-28l196-196-196-196q-11-11-11-28t11-28q11-11 28-11t28 11l196 196 196-196q11-11 28-11t28 11q11 11 11 28t-11 28L536-480l196 196q11 11 11 28t-11 28q-11 11-28 11t-28-11L480-424Z"
				/>
			</svg>
		</button>
		<div class="list-header-spacer"></div>
		<button type="button" class="iconbtn" aria-label="Edit" onclick={() => (editing = !editing)}>
			<svg viewBox="0 -960 960 960" fill="currentColor" aria-hidden="true">
				<path
					d="M200-200h57l391-391-57-57-391 391v57Zm-80 80v-170l528-527q11-12 26-18t31-6q16 0 30.5 6t25.5 18l55 56q12 11 18 25.5t6 30.5q0 16-6 31t-18 26L293-120H120Zm640-584-56-56 56 56Z"
				/>
			</svg>
		</button>
	</div>
	<BookHeader {entry} />
	{#if editing}
		<PhysicalOwnershipSection {entry} />
		<div class="confirm-actions">
			<button type="button" class="btn btn-ghost" onclick={() => (editing = false)}>Keep</button>
			<button type="button" class="btn btn-danger" disabled={removing} onclick={remove}>Remove from Shelf</button>
		</div>
	{/if}
</div>
