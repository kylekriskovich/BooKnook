<script lang="ts">
	import type { components } from '$lib/api/schema';
	import StatIcon from './StatIcon.svelte';

	type StatTile = components['schemas']['StatTileOut'];

	let {
		tiles,
		class: wrapperClass = 'stats-tile-grid',
		showIcons = false
	}: { tiles: StatTile[]; class?: string; showIcons?: boolean } = $props();
</script>

<div class={wrapperClass}>
	{#each tiles as tile (tile.label)}
		<div class="stats-tile">
			<div class="stats-tile-heading">
				{#if showIcons}
					<span class="stats-tile-icon"><StatIcon label={tile.label} /></span>
				{/if}
				<span class="stats-tile-label">{tile.label}</span>
			</div>
			<span class="stats-tile-value">{tile.value}</span>
			{#if tile.sub}<span class="stats-tile-sub">{tile.sub}</span>{/if}
		</div>
	{/each}
</div>
