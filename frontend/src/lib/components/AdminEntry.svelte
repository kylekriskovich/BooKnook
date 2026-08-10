<script lang="ts">
	import type { components } from '$lib/api/schema';

	type AdminEntry = components['schemas']['AdminEntryOut'];

	let { row, hideWantedBy = false }: { row: AdminEntry; hideWantedBy?: boolean } = $props();
</script>

<li class="entry-card">
	{#if row.cover_url}
		<img src={row.cover_url} alt="" class="cover" />
	{/if}
	<div class="entry-info">
		<strong>{row.title}</strong>
		{#if row.author}<span class="author">{row.author}</span>{/if}
		{#if row.wanted_by.length && !hideWantedBy}
			<span class="wanted-by">Wanted by: {row.wanted_by.join(', ')}</span>
		{/if}
	</div>
</li>
