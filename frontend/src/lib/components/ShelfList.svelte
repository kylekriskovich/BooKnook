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

	// Local, draggable copy of `entries` — resyncs on prop change, but not after this component's
	// own persistWantedOrder() call, so a drag reorder doesn't visually snap back mid round-trip.
	// svelte-ignore state_referenced_locally
	let entries = $state(initialEntries);
	$effect(() => {
		entries = initialEntries;
	});

	let editing = $state(false);
	const draggable = $derived(status === 'wanted' && editing);

	function handleConsider(event: CustomEvent<{ items: typeof entries }>) {
		entries = event.detail.items;
	}

	function handleFinalize(event: CustomEvent<{ items: typeof entries }>) {
		entries = event.detail.items;
		// Reconciles with the server's response rather than fire-and-forget, since predicted_month
		// (see monthMarkers below) depends on order and must be refreshed post-drop.
		persistWantedOrder(entries.map((e) => e.id)).then((fresh) => {
			entries = fresh;
		});
	}

	function monthLabel(yyyyMm: string): string {
		const [year, month] = yyyyMm.split('-').map(Number);
		return new Date(year, month - 1, 1).toLocaleDateString('en-US', { month: 'short' }).toUpperCase();
	}

	// Marks the last book still within a given projected month before the queue crosses into the
	// next one - null entries (no pace data yet) never trigger a marker (see stat_tiles
	// predicted_wanted_queue_months's server-side "hide entirely" behavior). Hidden entirely while
	// reordering, since dragging changes order live but predicted_month only updates once the
	// drop's persistWantedOrder response reconciles entries (handleFinalize above) - showing stale
	// predictions mid-drag would be misleading.
	const monthMarkers = $derived(
		status === 'wanted' && !editing
			? entries.map((entry, i) => {
					const previous = i > 0 ? entries[i - 1].predicted_month : null;
					return entry.predicted_month && entry.predicted_month !== previous
						? monthLabel(entry.predicted_month)
						: null;
				})
			: []
	);
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
		{#if status === 'wanted' && entries.length > 1}
			<button
				type="button"
				class="iconbtn"
				aria-label={editing ? 'Done reordering' : 'Reorder books'}
				onclick={() => (editing = !editing)}
			>
				{#if editing}
					<svg viewBox="0 -960 960 960" fill="currentColor" aria-hidden="true">
						<path d="M382-240 154-468l57-57 171 171 356-356 57 57-413 413Z" />
					</svg>
				{:else}
					<svg viewBox="0 -960 960 960" fill="currentColor" aria-hidden="true">
						<path
							d="M200-200h57l391-391-57-57-391 391v57Zm-80 80v-170l528-527q11-12 26-18t31-6q16 0 30.5 6t25.5 18l55 56q12 11 18 25.5t6 30.5q0 16-6 31t-18 26L293-120H120Zm640-584-56-56 56 56Z"
						/>
					</svg>
				{/if}
			</button>
		{/if}
	</div>

	{#if entries.length}
		<!-- Full shelf page has no toggle radios — view_preference picks one branch here directly. -->
		{#if auth.user?.view_preference === 'cover'}
			<div
				class="shelf-cover"
				use:dndzone={{ items: entries, dragDisabled: !draggable, flipDurationMs: 150, type: `shelf-list-${status}-cover` }}
				onconsider={handleConsider}
				onfinalize={handleFinalize}
			>
				{#each entries as entry, i (entry.id)}
					<div class="cover-book-cell">
						<CoverBook {entry} />
						{#if monthMarkers[i]}
							<div class="month-marker">
								<span class="month-marker-bar"></span>
								<span class="month-marker-label">{monthMarkers[i]}</span>
							</div>
						{/if}
					</div>
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
