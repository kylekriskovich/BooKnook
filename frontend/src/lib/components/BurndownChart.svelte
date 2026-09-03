<script lang="ts">
	import type { components } from '$lib/api/schema';
	import { burndownSvgPoints } from '$lib/utils/burndown';

	type BurndownPoint = components['schemas']['BurndownPointOut'];

	let { points, daySpan }: { points: BurndownPoint[]; daySpan: number } = $props();
</script>

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
		<polyline points={burndownSvgPoints(points)} fill="none" stroke="var(--accent)" stroke-width="2" />
	</svg>
</div>
<div class="burndown-x-axis">
	<span>Day 0</span>
	<span>Day {daySpan}</span>
</div>
