<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { api, unwrap } from '$lib/api/client';
	import type { components } from '$lib/api/schema';

	type SearchResult = components['schemas']['SearchResultOut'];

	let { result }: { result: SearchResult } = $props();

	let adding = $state(false);

	async function add() {
		adding = true;
		try {
			unwrap(
				await api.POST('/api/tbr', {
					body: {
						title: result.title,
						author: result.author ?? '',
						isbn: result.isbn ?? '',
						cover_url: result.cover_url ?? '',
						published_date: result.published_date ?? '',
						grimmory_id: result.grimmory_id != null ? String(result.grimmory_id) : ''
					}
				})
			);
			await invalidateAll();
			document.getElementById('add-sheet')?.hidePopover();
		} finally {
			adding = false;
		}
	}
</script>

<li class="result-card">
	{#if result.cover_url}
		<img src={result.cover_url} alt="" class="result-cover" />
	{/if}
	<div class="result-info">
		<strong>{result.title}</strong>
		{#if result.author}<span class="author">{result.author}</span>{/if}
	</div>
	<button type="button" class="btn btn-accent" disabled={adding} onclick={add}>Add</button>
</li>
