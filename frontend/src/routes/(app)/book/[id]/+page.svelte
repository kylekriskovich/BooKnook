<script lang="ts">
	import BookHeader from '$lib/components/BookHeader.svelte';
	import BookStatsPanel from '$lib/components/BookStatsPanel.svelte';
	import ReadingDatesSection from '$lib/components/ReadingDatesSection.svelte';

	let { data } = $props();
	let entry = $derived(data.detail.entry);
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
	<span class="list-title">{entry.book.title}</span>
</div>

<BookHeader {entry} />

{#key entry.id}
	<ReadingDatesSection {entry} />
{/key}

{#if entry.has_paired_audiobook}
	<input type="radio" name="book-view" id="book-view-reading" class="view-radio" checked />
	<input type="radio" name="book-view" id="book-view-listening" class="view-radio" />

	<div class="view-tabs book-view-tabs">
		<label for="book-view-reading" class="view-tab">READING</label>
		<label for="book-view-listening" class="view-tab">LISTENING</label>
	</div>
{/if}

<div class="book-view-reading-section">
	<BookStatsPanel
		progressPercent={data.detail.progress_percent}
		estimatedPage={data.detail.estimated_page}
		pageCount={entry.book.page_count}
		tiles={data.detail.tiles}
		burndown={data.detail.burndown}
		burndownDaySpan={data.detail.burndown_day_span}
		statsTitle="Reading stats"
		burndownTitle="Reading progress"
		emptyStateText="No reading-session data yet for this book."
	/>
</div>

{#if entry.has_paired_audiobook}
	<div class="book-view-listening-section">
		<BookStatsPanel
			progressPercent={data.detail.audiobook_progress_percent}
			estimatedPage={data.detail.audiobook_estimated_page}
			pageCount={entry.book.page_count}
			tiles={data.detail.audiobook_tiles}
			burndown={data.detail.audiobook_burndown}
			burndownDaySpan={data.detail.audiobook_burndown_day_span}
			statsTitle="Listening stats"
			burndownTitle="Listening progress"
			emptyStateText="No listening-session data yet for this book."
		/>
	</div>
{/if}
