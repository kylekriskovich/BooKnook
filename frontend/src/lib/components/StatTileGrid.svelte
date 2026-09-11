<script lang="ts">
	import type { components } from '$lib/api/schema';

	type StatTile = components['schemas']['StatTileOut'];

	let { tiles, class: wrapperClass = 'stats-tile-grid' }: { tiles: StatTile[]; class?: string } =
		$props();
</script>

<div class={wrapperClass}>
	{#each tiles as tile (tile.label)}
		<div class="stats-tile">
			<span class="stats-tile-label">{tile.label}</span>
			<span class="stats-tile-value">{tile.value}</span>
			{#if tile.sub}<span class="stats-tile-sub">{tile.sub}</span>{/if}
		</div>
	{/each}
</div>
