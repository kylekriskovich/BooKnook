<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { api, ApiError, unwrap } from '$lib/api/client';
	import type { components } from '$lib/api/schema';
	import { adminMatchTarget } from '$lib/stores/adminMatch.svelte';

	type AdminEntry = components['schemas']['AdminEntryOut'];

	let {
		row,
		mode,
		matchable = true
	}: { row: AdminEntry; mode: 'needed' | 'owned'; matchable?: boolean } = $props();

	let unmatching = $state(false);
	let unmatchError = $state<string | null>(null);

	function openMatchSheet() {
		if (row.id == null) return;
		adminMatchTarget.set(row.id, row.title);
	}

	async function unmatch() {
		if (row.id == null) return;
		unmatching = true;
		unmatchError = null;
		try {
			unwrap(
				await api.POST('/api/admin/books/{book_id}/match', {
					params: { path: { book_id: row.id } },
					body: { grimmory_id: null }
				})
			);
			await invalidateAll();
		} catch (err) {
			unmatchError = err instanceof ApiError ? err.message : 'Could not reach the server — try again.';
		} finally {
			unmatching = false;
		}
	}
</script>

<li class="entry-card">
	{#if row.cover_url}
		<img src={row.cover_url} alt="" class="cover" />
	{/if}
	<div class="entry-info">
		<strong>{row.title}</strong>
		{#if row.author}<span class="author">{row.author}</span>{/if}
		{#if row.wanted_by.length && mode === 'needed'}
			<span class="wanted-by">Wanted by: {row.wanted_by.join(', ')}</span>
		{/if}
		{#if unmatchError}<span class="error">{unmatchError}</span>{/if}
	</div>
	{#if mode === 'needed' && matchable && row.id != null}
		<button
			type="button"
			class="btn btn-ghost"
			popovertarget="admin-match-sheet"
			onclick={openMatchSheet}
		>
			Match
		</button>
	{:else if mode === 'owned' && row.manually_matched}
		<button type="button" class="btn btn-ghost" disabled={unmatching} onclick={unmatch}>
			Unmatch
		</button>
	{/if}
</li>
