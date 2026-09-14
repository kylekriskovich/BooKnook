<script lang="ts">
	import type { components } from '$lib/api/schema';
	import { burndownSvgCoords, burndownSvgPoints } from '$lib/utils/burndown';

	type BurndownPoint = components['schemas']['BurndownPointOut'];

	let { points, daySpan }: { points: BurndownPoint[]; daySpan: number } = $props();

	let coords = $derived(burndownSvgCoords(points));

	// Solid gridlines and labels every 20%, with a faint dotted gridline at the 10% mark between each.
	const yGridlines = [0, 20, 40, 60, 80, 100];
	const yMidGridlines = [10, 30, 50, 70, 90];
	const yLabels = yGridlines;

	// A notch on the x-axis for every day boundary except day 0 and the final day - day 0 sits on
	// the chart's left border already, and the final day already gets its own "Day N" label, so a
	// notch under it would be redundant. Interior days get a plain notch, keeping the axis readable
	// regardless of how many days it spans.
	let dayLines = $derived.by(() => {
		const days: number[] = [];
		for (let d = 1; d < daySpan; d++) days.push(d);
		return days;
	});
</script>

<div class="burndown-grid">
	<div class="burndown-y-axis">
		{#each yLabels as pct (pct)}
			<span
				class="burndown-y-label"
				style="top: {pct}%; transform: translateY({pct === 0 ? '0' : pct === 100 ? '-100%' : '-50%'})"
			>
				{pct}%
			</span>
		{/each}
	</div>
	<svg class="burndown-chart" viewBox="0 0 300 100" preserveAspectRatio="none">
		{#each yGridlines as pct (pct)}
			<line class="burndown-gridline" x1="0" y1={pct} x2="300" y2={pct} />
		{/each}
		{#each yMidGridlines as pct (pct)}
			<line class="burndown-gridline burndown-gridline-mid" x1="0" y1={pct} x2="300" y2={pct} />
		{/each}
		<polyline points={burndownSvgPoints(points)} fill="none" stroke="var(--accent)" stroke-width="2" />
		{#each coords as coord, i (i)}
			<circle class="burndown-node" cx={coord.x} cy={coord.y} r="2.5" />
		{/each}
	</svg>
	<div class="burndown-x-axis">
		{#each dayLines as day (day)}
			<span class="burndown-day-notch" style="left: {(day / daySpan) * 100}%"></span>
		{/each}
		{#if daySpan > 0}
			<span class="burndown-tick" style="left: 100%; transform: translateX(-100%)">
				Day {daySpan}
			</span>
		{/if}
	</div>
</div>
