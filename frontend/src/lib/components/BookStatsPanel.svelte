<script lang="ts">
	import type { components } from '$lib/api/schema';
	import BurndownChart from './BurndownChart.svelte';
	import StatTileGrid from './StatTileGrid.svelte';

	type StatTile = components['schemas']['StatTileOut'];
	type BurndownPoint = components['schemas']['BurndownPointOut'];

	let {
		progressPercent,
		estimatedPage,
		pageCount,
		tiles,
		burndown,
		burndownDaySpan,
		statsTitle,
		burndownTitle,
		emptyStateText
	}: {
		progressPercent: number | null | undefined;
		estimatedPage: number | null | undefined;
		pageCount: number | null | undefined;
		tiles: StatTile[];
		burndown: BurndownPoint[];
		burndownDaySpan: number;
		statsTitle: string;
		burndownTitle: string;
		emptyStateText: string;
	} = $props();
</script>

{#if progressPercent !== null && progressPercent !== undefined}
	<div class="settings-section">
		<div class="settings-section-title">Current progress</div>
		<div class="progress-bar-track">
			<span class="progress-bar-fill" style="width: {progressPercent}%"></span>
		</div>
		<div class="progress-bar-label">
			<span>{progressPercent.toFixed(1)}%</span>
			{#if estimatedPage}
				<span>~page {estimatedPage} of {pageCount}</span>
			{/if}
		</div>
		{#if estimatedPage}
			<p class="empty-state">Estimated page number based on completion percentage.</p>
		{/if}
	</div>
{/if}

{#if tiles.length}
	<div class="settings-section">
		<div class="settings-section-title">{statsTitle}</div>
		<StatTileGrid {tiles} />
	</div>
{/if}

{#if burndown.length > 1}
	<div class="settings-section">
		<div class="settings-section-title">{burndownTitle}</div>
		<BurndownChart points={burndown} daySpan={burndownDaySpan} />
	</div>
{/if}

{#if !tiles.length && !burndown.length}
	<p class="empty-state">{emptyStateText}</p>
{/if}
