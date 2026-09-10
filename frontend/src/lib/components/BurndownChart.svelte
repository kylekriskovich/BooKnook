<script lang="ts">
	import type { components } from '$lib/api/schema';
	import { burndownSvgPoints } from '$lib/utils/burndown';

	type BurndownPoint = components['schemas']['BurndownPointOut'];

	let { points, daySpan }: { points: BurndownPoint[]; daySpan: number } = $props();

	// Up to 5 evenly-spaced day labels (matching the y-axis's own 5 gridlines), always including
	// day 0 and daySpan - for a short span (e.g. daySpan=2) this lands on every day; for a long one
	// it thins out rather than cramming a label per day.
	let ticks = $derived.by(() => {
		if (daySpan <= 0) return [0];
		const steps = Math.min(daySpan, 4);
		const days = new Set<number>();
		for (let i = 0; i <= steps; i++) days.add(Math.round((daySpan * i) / steps));
		return [...days].sort((a, b) => a - b);
	});
</script>

<div class="burndown-grid">
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
		<polyline points={burndownSvgPoints(points)} fill="none" stroke="var(--accent)" stroke-width="2" />
	</svg>
	<div class="burndown-x-axis">
		{#each ticks as tick, i (tick)}
			<span
				class="burndown-tick"
				style="left: {daySpan > 0 ? (tick / daySpan) * 100 : 0}%; transform: translateX({i === 0
					? '0'
					: i === ticks.length - 1
						? '-100%'
						: '-50%'})"
			>
				Day {tick}
			</span>
		{/each}
	</div>
</div>
