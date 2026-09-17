<script lang="ts">
	import BookHeader from '$lib/components/BookHeader.svelte';
	import PhysicalOwnershipSection from '$lib/components/PhysicalOwnershipSection.svelte';
	import PhysicalReadingSessionsSection from '$lib/components/PhysicalReadingSessionsSection.svelte';
	import ProgressSection from '$lib/components/ProgressSection.svelte';
	import ReadingDatesSection from '$lib/components/ReadingDatesSection.svelte';
	import SessionLogModal from '$lib/components/SessionLogModal.svelte';
	import StatTileGrid from '$lib/components/StatTileGrid.svelte';

	let { data } = $props();
	let entry = $derived(data.detail.entry);
	let physicalSectionRef = $state<ReturnType<typeof PhysicalReadingSessionsSection>>();
	// entry is a fresh object on every invalidateAll() reload - deriving the id separately means
	// the reset effect below only re-fires when the number actually changes, not on every save.
	let entryId = $derived(entry.id);

	// Owned here, not inside ReadingDatesSection, because PhysicalOwnershipSection (a separate
	// component, rendered above it) needs to show/hide in lockstep with the same edit mode.
	let editingDates = $state(false);
	$effect(() => {
		entryId; // dependency only - reset edit mode whenever the viewed book actually changes
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
	{#if entry.owns_physical}
		<button
			type="button"
			class="iconbtn"
			aria-label="Log a physical reading session"
			popovertarget="physical-session-form-{entry.id}"
		>
			<svg viewBox="0 -960 960 960" fill="currentColor" aria-hidden="true">
				<path d="M440-440H240v-80h200v-200h80v200h200v80H520v200h-80v-200Z" />
			</svg>
		</button>
	{/if}
	<button
		type="button"
		class="iconbtn"
		aria-label="View session log"
		popovertarget="session-log-{entry.id}"
	>
		<svg viewBox="0 -960 960 960" fill="currentColor" aria-hidden="true">
			<path
				d="M480-120q-138 0-240.5-91.5T122-440h82q14 104 92.5 172T480-200q117 0 198.5-81.5T760-480q0-117-81.5-198.5T480-760q-69 0-129 32t-101 88h110v80H120v-240h80v94q51-64 124.5-102T480-840q75 0 140.5 28.5t114 77q48.5 48.5 77 114T840-480q0 75-28.5 140.5t-77 114q-48.5 48.5-114 77T480-120Zm112-192L440-464v-216h80v184l128 128-56 56Z"
			/>
		</svg>
	</button>
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
	{#if entry.owns_physical}
		<PhysicalReadingSessionsSection bind:this={physicalSectionRef} {entry} />
	{/if}
	<SessionLogModal
		{entry}
		sessions={data.sessionLog}
		onEditPhysical={(session) => physicalSectionRef?.openForEdit(session)}
	/>
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
