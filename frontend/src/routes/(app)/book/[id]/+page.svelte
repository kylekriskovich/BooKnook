<script lang="ts">
	import BookHeader from '$lib/components/BookHeader.svelte';
	import PhysicalOwnershipSection from '$lib/components/PhysicalOwnershipSection.svelte';
	import ProgressSection from '$lib/components/ProgressSection.svelte';
	import ReadingDatesSection from '$lib/components/ReadingDatesSection.svelte';
	import StatTileGrid from '$lib/components/StatTileGrid.svelte';

	let { data } = $props();
	let entry = $derived(data.detail.entry);

	// Owned here, not inside ReadingDatesSection, because PhysicalOwnershipSection (a separate
	// component, rendered above it) needs to show/hide in lockstep with the same edit mode.
	let editingDates = $state(false);
	$effect(() => {
		entry.id; // dependency only - reset edit mode whenever the viewed book changes
		editingDates = false;
	});
</script>

<svelte:head>
	<title>{entry.book.title} — Book Knook</title>
</svelte:head>

<div class="list-header">
	<a href="/shelf/{entry.status}" class="iconbtn" aria-label="Back">
		<svg viewBox="0 -960 960 960" fill="currentColor" aria-hidden="true">
			<path d="M400-80 0-480l400-400 71 71-329 329 329 329-71 71Z" />
		</svg>
	</a>
	<!-- Title already shown by BookHeader below, right next to the cover - not repeated here. -->
	<div class="list-header-spacer"></div>
	<button
		type="button"
		class="iconbtn"
		aria-label="Edit reading dates"
		onclick={() => (editingDates = !editingDates)}
	>
		<svg viewBox="0 -960 960 960" fill="currentColor" aria-hidden="true">
			<path
				d="M200-200h57l391-391-57-57-391 391v57Zm-80 80v-170l528-527q11-12 26-18t31-6q16 0 30.5 6t25.5 18l55 56q12 11 18 25.5t6 30.5q0 16-6 31t-18 26L293-120H120Zm640-584-56-56 56 56Z"
			/>
		</svg>
	</button>
</div>

<BookHeader {entry} />

{#key entry.id}
	{#if editingDates}
		<PhysicalOwnershipSection {entry} />
		<ReadingDatesSection {entry} bind:editingDates />
	{/if}
{/key}

{#if !editingDates}
	<ProgressSection
		progressPercent={data.detail.progress_percent}
		estimatedPage={data.detail.estimated_page}
		pageCount={entry.book.page_count}
		burndown={data.detail.burndown}
		burndownDaySpan={data.detail.burndown_day_span}
	/>

	{#if entry.has_paired_audiobook}
		<input type="radio" name="book-view" id="book-view-reading" class="view-radio" checked />
		<input type="radio" name="book-view" id="book-view-listening" class="view-radio" />

		<div class="view-tabs book-view-tabs">
			<label for="book-view-reading" class="view-tab">READING</label>
			<label for="book-view-listening" class="view-tab">LISTENING</label>
		</div>
	{/if}

	<div class="book-view-reading-section">
		{#if data.detail.tiles.length}
			<div class="settings-section">
				<div class="settings-section-title">Reading stats</div>
				<StatTileGrid tiles={data.detail.tiles} />
			</div>
		{:else}
			<p class="empty-state">No reading-session data yet for this book.</p>
		{/if}
	</div>

	{#if entry.has_paired_audiobook}
		<div class="book-view-listening-section">
			{#if data.detail.audiobook_tiles.length}
				<div class="settings-section">
					<div class="settings-section-title">Listening stats</div>
					<StatTileGrid tiles={data.detail.audiobook_tiles} />
				</div>
			{:else}
				<p class="empty-state">No listening-session data yet for this book.</p>
			{/if}
		</div>
	{/if}
{/if}
