<script lang="ts">
	import type { components } from '$lib/api/schema';
	import BurndownChart from './BurndownChart.svelte';

	type BurndownPoint = components['schemas']['BurndownPointOut'];

	// Unified across every linked edition (DESIGN-multi-edition-refactor.md Decision 5) — one
	// "how far into this book am I" figure and one progress-over-time line, not a separate pair
	// per medium the way Reading/Listening tiles below still are.
	let {
		progressPercent,
		estimatedPage,
		pageCount,
		burndown,
		burndownDaySpan
	}: {
		progressPercent: number | null | undefined;
		estimatedPage: number | null | undefined;
		pageCount: number | null | undefined;
		burndown: BurndownPoint[];
		burndownDaySpan: number;
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
	</div>
{/if}

{#if burndown.length > 1}
	<div class="settings-section">
		<div class="settings-section-title">Reading progress</div>
		<BurndownChart points={burndown} daySpan={burndownDaySpan} />
	</div>
{/if}
