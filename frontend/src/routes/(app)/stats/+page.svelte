<script lang="ts">
	import StatTileGrid from '$lib/components/StatTileGrid.svelte';

	let { data } = $props();
</script>

<svelte:head>
	<title>Stats — Book Knook</title>
</svelte:head>

<div class="list-header">
	<span class="list-title">Stats</span>
</div>

{#if data.stats.goal}
	<div class="settings-section">
		<div class="settings-section-title">{data.stats.year} reading goal</div>
		<div class="settings-list">
			<div class="settings-row">
				<span class="settings-row-label">Finished</span>
				<span class="settings-row-value">{data.stats.finished_count} / {data.stats.goal.target_count} books</span>
			</div>
		</div>
	</div>
{:else}
	<p class="empty-state">You can set a reading goal in settings.</p>
{/if}

{#if data.stats.tile_groups.overview.length || data.stats.tile_groups.averages.length || data.stats.tile_groups.highlights.length}
	<input type="radio" name="stats-view" id="stats-view-overview" class="view-radio" checked />
	<input type="radio" name="stats-view" id="stats-view-averages" class="view-radio" />
	<input type="radio" name="stats-view" id="stats-view-highlights" class="view-radio" />

	<div class="view-tabs stats-view-tabs">
		<label for="stats-view-overview" class="view-tab">
			<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
				<rect x="3" y="3" width="8" height="8" rx="1" />
				<rect x="13" y="3" width="8" height="8" rx="1" />
				<rect x="3" y="13" width="8" height="8" rx="1" />
				<rect x="13" y="13" width="8" height="8" rx="1" />
			</svg>
			<span class="view-tab-label">Overview</span>
		</label>
		<label for="stats-view-averages" class="view-tab">
			<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
				<rect x="4" y="13" width="4" height="7" />
				<rect x="10" y="8" width="4" height="12" />
				<rect x="16" y="3" width="4" height="17" />
			</svg>
			<span class="view-tab-label">Averages</span>
		</label>
		<label for="stats-view-highlights" class="view-tab">
			<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
				<polygon
					points="12,3 14.12,9.09 20.56,9.22 15.42,13.11 17.29,19.28 12,15.6 6.71,19.28 8.58,13.11 3.44,9.22 9.88,9.09"
				/>
			</svg>
			<span class="view-tab-label">Highlights</span>
		</label>
	</div>

	<div class="stats-view-overview-section">
		{#if data.stats.tile_groups.overview.length}
			<div class="settings-section">
				<StatTileGrid tiles={data.stats.tile_groups.overview} class="stats-tile-grid-3col" showIcons />
			</div>
		{:else}
			<p class="empty-state">No data yet.</p>
		{/if}
	</div>

	<div class="stats-view-averages-section">
		{#if data.stats.tile_groups.averages.length}
			<div class="settings-section">
				<StatTileGrid tiles={data.stats.tile_groups.averages} class="stats-tile-grid-3col" showIcons />
			</div>
		{:else}
			<p class="empty-state">No data yet.</p>
		{/if}
	</div>

	<div class="stats-view-highlights-section">
		{#if data.stats.tile_groups.highlights.length}
			<div class="settings-section">
				<StatTileGrid tiles={data.stats.tile_groups.highlights} class="stats-tile-grid-3col" showIcons />
			</div>
		{:else}
			<p class="empty-state">No data yet.</p>
		{/if}
	</div>
{/if}
