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

{#if data.stats.tiles.length}
	<div class="settings-section">
		<div class="settings-section-title">{data.stats.year} so far</div>
		<StatTileGrid tiles={data.stats.tiles} />
	</div>
{/if}
