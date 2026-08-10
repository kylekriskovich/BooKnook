<script lang="ts">
	import BookHeader from '$lib/components/BookHeader.svelte';
	import ReadingDatesSection from '$lib/components/ReadingDatesSection.svelte';
	import StatTileGrid from '$lib/components/StatTileGrid.svelte';
	import { burndownSvgPoints } from '$lib/utils/burndown';

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

{#if data.detail.progress_percent !== null && data.detail.progress_percent !== undefined}
	<div class="settings-section">
		<div class="settings-section-title">Current progress</div>
		<div class="progress-bar-track">
			<span class="progress-bar-fill" style="width: {data.detail.progress_percent}%"></span>
		</div>
		<div class="progress-bar-label">
			<span>{data.detail.progress_percent.toFixed(1)}%</span>
			{#if data.detail.estimated_page}
				<span>~page {data.detail.estimated_page} of {entry.book.page_count}</span>
			{/if}
		</div>
		{#if data.detail.estimated_page}
			<p class="empty-state">Estimated page number based on completion percentage.</p>
		{/if}
	</div>
{/if}

{#key entry.id}
	<ReadingDatesSection {entry} />
{/key}

{#if data.detail.tiles.length}
	<div class="settings-section">
		<div class="settings-section-title">Reading stats</div>
		<StatTileGrid tiles={data.detail.tiles} />
	</div>
{/if}

{#if data.detail.burndown.length > 1}
	<div class="settings-section">
		<div class="settings-section-title">Reading progress</div>
		<div class="burndown-wrap">
			<div class="burndown-y-axis">
				<span>100%</span>
				<span>75%</span>
				<span>50%</span>
				<span>25%</span>
				<span>0%</span>
			</div>
			<svg class="burndown-chart" viewBox="0 0 300 100" preserveAspectRatio="none">
				<line class="burndown-gridline" x1="0" y1="0" x2="300" y2="0" />
				<line class="burndown-gridline" x1="0" y1="25" x2="300" y2="25" />
				<line class="burndown-gridline" x1="0" y1="50" x2="300" y2="50" />
				<line class="burndown-gridline" x1="0" y1="75" x2="300" y2="75" />
				<line class="burndown-gridline" x1="0" y1="100" x2="300" y2="100" />
				<polyline points={burndownSvgPoints(data.detail.burndown)} fill="none" stroke="var(--accent)" stroke-width="2" />
			</svg>
		</div>
		<div class="burndown-x-axis">
			<span>Day 0</span>
			<span>Day {data.detail.burndown_day_span}</span>
		</div>
	</div>
{/if}

{#if !data.detail.tiles.length && !data.detail.burndown.length}
	<p class="empty-state">No reading-session data yet for this book.</p>
{/if}
