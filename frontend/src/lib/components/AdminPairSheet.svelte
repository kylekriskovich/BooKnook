<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { api, ApiError, unwrap } from '$lib/api/client';
	import { adminPairTarget } from '$lib/stores/adminPair.svelte';
	import type { components } from '$lib/api/schema';

	type SearchResult = components['schemas']['SearchResultOut'];

	let query = $state('');
	let results = $state<SearchResult[]>([]);
	let hasSearched = $state(false);
	let pairingGrimmoryId = $state<number | null>(null);
	let pairError = $state<string | null>(null);
	let searchError = $state<string | null>(null);

	let searchTimer: ReturnType<typeof setTimeout> | undefined = undefined;

	// Grimmory audiobook editions are typically the ebook's own title plus a trailing parenthetical
	// ("(10th Anniversary Recording)", "(Unabridged)", ...) - stripping it before pre-filling the
	// search gives a query that's actually a substring of the ebook's title (see
	// search_library_catalog's LIKE %query%, which needs that to match at all).
	function baseTitle(title: string): string {
		return title.replace(/\s*\([^)]*\)\s*$/, '').trim();
	}

	// Reset search state each time a different audiobook is targeted, so stale results from a
	// previous pairing attempt never show for the newly-opened row - then pre-fill and run a search
	// from the audiobook's own (cleaned) title as a starting point. Computes the prefill into a
	// local instead of reading `query` back after writing it - reading query here too would make
	// this effect depend on its own output, so every edit (including backspacing) would immediately
	// re-trigger it and reset the input right back to the prefilled title.
	$effect(() => {
		void adminPairTarget.audiobookGrimmoryId;
		const prefill = baseTitle(adminPairTarget.title);
		query = prefill;
		results = [];
		hasSearched = false;
		pairError = null;
		searchError = null;
		if (prefill) search(prefill);
	});

	function onInput() {
		clearTimeout(searchTimer);
		searchTimer = setTimeout(() => search(query), 150);
	}

	async function search(q: string) {
		q = q.trim();
		if (!q) {
			results = [];
			hasSearched = false;
			searchError = null;
			return;
		}
		try {
			const data = unwrap(
				await api.GET('/api/admin/library-search', {
					params: { query: { q, exclude_audiobooks: true } }
				})
			);
			results = data.results;
			searchError = null;
		} catch (err) {
			results = [];
			searchError = err instanceof ApiError ? err.message : 'Could not reach the server — try again.';
		} finally {
			hasSearched = true;
		}
	}

	function onSubmit(event: SubmitEvent) {
		event.preventDefault();
		clearTimeout(searchTimer);
		search(query);
	}

	async function pair(result: SearchResult) {
		if (adminPairTarget.audiobookGrimmoryId == null || result.grimmory_id == null) return;
		pairingGrimmoryId = result.grimmory_id;
		pairError = null;
		try {
			unwrap(
				await api.POST('/api/admin/audiobooks/{audiobook_grimmory_id}/pair', {
					params: { path: { audiobook_grimmory_id: adminPairTarget.audiobookGrimmoryId } },
					body: { ebook_grimmory_id: result.grimmory_id }
				})
			);
			await invalidateAll();
			document.getElementById('admin-pair-sheet')?.hidePopover();
		} catch (err) {
			pairError = err instanceof ApiError ? err.message : 'Could not reach the server — try again.';
		} finally {
			pairingGrimmoryId = null;
		}
	}
</script>

<div id="admin-pair-sheet" popover class="sheet">
	<div class="sheet-handle"></div>
	<div class="sheet-title">PAIR "{adminPairTarget.title}" TO EBOOK</div>

	<form class="search-form" onsubmit={onSubmit}>
		<input
			type="search"
			placeholder="Search your library"
			autocomplete="off"
			bind:value={query}
			oninput={onInput}
		/>
		<button type="submit">Search</button>
	</form>

	{#if pairError}<p class="error">{pairError}</p>{/if}
	{#if searchError}<p class="error">{searchError}</p>{/if}

	{#if results.length}
		<ul class="search-results-list">
			{#each results as result, index (result.grimmory_id ?? result.isbn ?? index)}
				<li class="result-card">
					{#if result.cover_url}
						<img src={result.cover_url} alt="" class="result-cover" />
					{/if}
					<div class="result-info">
						<strong>{result.title}</strong>
						{#if result.author}<span class="author">{result.author}</span>{/if}
					</div>
					<button
						type="button"
						class="btn btn-accent"
						disabled={pairingGrimmoryId != null || result.grimmory_id == null}
						onclick={() => pair(result)}
					>
						Pair
					</button>
				</li>
			{/each}
		</ul>
	{:else if hasSearched && query.trim() && !searchError}
		<p class="empty-state">No results for "{query.trim()}".</p>
	{/if}
</div>
