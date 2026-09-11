<script lang="ts">
	import type { components } from '$lib/api/schema';
	import { auth } from '$lib/stores/auth.svelte';
	import SpineBook from './SpineBook.svelte';
	import CoverBook from './CoverBook.svelte';
	import BookModal from './BookModal.svelte';

	type Shelf = components['schemas']['ShelfOut'];

	let { shelf }: { shelf: Shelf } = $props();

	// No drag-and-drop here — svelte-dnd-action's touch handling fights with vertical page
	// scrolling on mobile, which is this row's primary surface. Reordering only lives on the full
	// /shelf/wanted page (ShelfList.svelte), which isn't squeezed between other scrollable rows.
</script>

<div class="shelf-row">
	<a href="/shelf/{shelf.status}" class="shelf-row-header">
		<span class="shelf-row-name">{shelf.label.toUpperCase()}</span>
		<span class="shelf-row-count">{shelf.entries.length}</span>
	</a>

	{#if shelf.entries.length}
		{#if auth.user?.view_preference === 'cover'}
			<div class="mini-shelf-cover">
				{#each shelf.entries as entry (entry.id)}
					<CoverBook {entry} />
				{/each}
			</div>
		{:else}
			<div class="shelf-spine">
				<ul class="spine-row">
					{#each shelf.entries as entry (entry.id)}
						<li><SpineBook {entry} /></li>
					{/each}
				</ul>
				<div class="shelf-ledge"></div>
			</div>
		{/if}

		{#each shelf.entries.filter((e) => e.status === 'wanted') as entry (entry.id)}
			<BookModal {entry} />
		{/each}
	{:else}
		<p class="shelf-row-empty">Nothing here yet.</p>
	{/if}
</div>
