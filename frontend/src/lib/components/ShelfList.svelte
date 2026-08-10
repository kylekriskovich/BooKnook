<script lang="ts">
	import { dndzone } from 'svelte-dnd-action';
	import type { components } from '$lib/api/schema';
	import { auth } from '$lib/stores/auth.svelte';
	import { persistWantedOrder } from '$lib/utils/reorder';
	import SpineBook from './SpineBook.svelte';
	import CoverBook from './CoverBook.svelte';
	import BookModal from './BookModal.svelte';

	type TBREntry = components['schemas']['TBREntryOut'];

	let { status, label, entries: initialEntries }: { status: string; label: string; entries: TBREntry[] } =
		$props();

	// Local, draggable copy — see ShelfRow.svelte's identical comment for why this needs its own
	// state instead of reordering the `entries` prop directly, and why it resyncs on prop change
	// but not after this component's own persistWantedOrder() call.
	// svelte-ignore state_referenced_locally
	let entries = $state(initialEntries);
	$effect(() => {
		entries = initialEntries;
	});

	const draggable = $derived(status === 'wanted');

	function handleConsider(event: CustomEvent<{ items: typeof entries }>) {
		entries = event.detail.items;
	}

	function handleFinalize(event: CustomEvent<{ items: typeof entries }>) {
		entries = event.detail.items;
		persistWantedOrder(entries.map((e) => e.id));
	}
</script>

<section id="shelf-list">
	<div class="list-header">
		<a href="/home" class="iconbtn" aria-label="Back to home">
			<svg viewBox="0 -960 960 960" fill="currentColor" aria-hidden="true">
				<path d="M400-80 0-480l400-400 71 71-329 329 329 329-71 71Z" />
			</svg>
		</a>
		<span class="list-title">{label}</span>
		<span class="list-count">{entries.length}</span>
	</div>

	{#if entries.length}
		<!-- Full shelf page has no toggle radios — view_preference picks one branch here, same as
		     app/templates/_shelf_books.html; app.css's #shelf-list .shelf-spine/.shelf-cover rules
		     always show whichever one renders. -->
		{#if auth.user?.view_preference === 'cover'}
			<div
				class="shelf-cover"
				use:dndzone={{ items: entries, dragDisabled: !draggable, flipDurationMs: 150, type: `shelf-list-${status}-cover` }}
				onconsider={handleConsider}
				onfinalize={handleFinalize}
			>
				{#each entries as entry (entry.id)}
					<CoverBook {entry} />
				{/each}
			</div>
		{:else}
			<div class="shelf-spine">
				<ul
					class="spine-row"
					use:dndzone={{ items: entries, dragDisabled: !draggable, flipDurationMs: 150, type: `shelf-list-${status}-spine` }}
					onconsider={handleConsider}
					onfinalize={handleFinalize}
				>
					{#each entries as entry (entry.id)}
						<li><SpineBook {entry} /></li>
					{/each}
				</ul>
				<div class="shelf-ledge"></div>
			</div>
		{/if}

		{#each entries.filter((e) => e.status === 'wanted') as entry (entry.id)}
			<BookModal {entry} />
		{/each}
	{:else}
		<p class="empty-state">Nothing here yet.</p>
	{/if}
</section>
