<script lang="ts">
	import { dndzone } from 'svelte-dnd-action';
	import type { components } from '$lib/api/schema';
	import { persistWantedOrder } from '$lib/utils/reorder';
	import SpineBook from './SpineBook.svelte';
	import CoverBook from './CoverBook.svelte';
	import BookModal from './BookModal.svelte';

	type Shelf = components['schemas']['ShelfOut'];

	let { shelf }: { shelf: Shelf } = $props();

	// Local, draggable copy of the shelf's entries — svelte-dnd-action needs to own an array it
	// can reorder live during a drag. Re-synced whenever a fresh `shelf` prop arrives (a real
	// server reload), but *not* after a drag's own persistWantedOrder() call below, which
	// deliberately doesn't trigger a refetch — see that function's comment. The $effect below
	// covers the initial value too (redundant with, not stale relative to, the line below), so
	// capturing it locally here is intentional, not the usual state-goes-stale bug this warns about.
	// svelte-ignore state_referenced_locally
	let entries = $state(shelf.entries);
	$effect(() => {
		entries = shelf.entries;
	});

	const draggable = $derived(shelf.status === 'wanted');

	// Both the spine list and the cover grid below render this same `entries` array (see
	// (app)/+layout.svelte's CSS-only spine/cover toggle) — a drag finished in either one must
	// keep both in sync, so there's a single dndzone options object and a single pair of handlers
	// shared by both containers.
	function handleConsider(event: CustomEvent<{ items: typeof entries }>) {
		entries = event.detail.items;
	}

	function handleFinalize(event: CustomEvent<{ items: typeof entries }>) {
		entries = event.detail.items;
		persistWantedOrder(entries.map((e) => e.id));
	}
</script>

<div class="shelf-row">
	<a href="/shelf/{shelf.status}" class="shelf-row-header">
		<span class="shelf-row-name">{shelf.label.toUpperCase()}</span>
		<span class="shelf-row-count">{entries.length}</span>
	</a>

	{#if entries.length}
		<!-- Both spine and cover views render unconditionally; app.css's #view-spine:checked /
		     #view-cover:checked sibling selectors (see (app)/+layout.svelte) show only one — same
		     CSS-only toggle the Jinja2/htmx app used, so switching views never needs a re-fetch. -->
		<div class="shelf-spine">
			<ul
				class="spine-row"
				use:dndzone={{ items: entries, dragDisabled: !draggable, flipDurationMs: 150, type: `shelf-${shelf.status}-spine` }}
				onconsider={handleConsider}
				onfinalize={handleFinalize}
			>
				{#each entries as entry (entry.id)}
					<li><SpineBook {entry} /></li>
				{/each}
			</ul>
			<div class="shelf-ledge"></div>
		</div>

		<div
			class="mini-shelf-cover"
			use:dndzone={{ items: entries, dragDisabled: !draggable, flipDurationMs: 150, type: `shelf-${shelf.status}-cover` }}
			onconsider={handleConsider}
			onfinalize={handleFinalize}
		>
			{#each entries as entry (entry.id)}
				<CoverBook {entry} />
			{/each}
		</div>

		{#each entries.filter((e) => e.status === 'wanted') as entry (entry.id)}
			<BookModal {entry} />
		{/each}
	{:else}
		<p class="shelf-row-empty">Nothing here yet.</p>
	{/if}
</div>
