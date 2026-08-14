<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { api, ApiError, unwrap } from '$lib/api/client';
	import { adminMatchTarget } from '$lib/stores/adminMatch.svelte';
	import type { components } from '$lib/api/schema';

	type SearchResult = components['schemas']['SearchResultOut'];

	let query = $state('');
	let results = $state<SearchResult[]>([]);
	let hasSearched = $state(false);
	let matchingGrimmoryId = $state<number | null>(null);
	let matchError = $state<string | null>(null);

	let searchTimer: ReturnType<typeof setTimeout>;

	// Reset search state each time a different book is targeted, so stale results from a previous
	// match attempt never show for the newly-opened row.
	$effect(() => {
		void adminMatchTarget.bookId;
		query = '';
		results = [];
		hasSearched = false;
		matchError = null;
	});

	function onInput() {
		clearTimeout(searchTimer);
		searchTimer = setTimeout(search, 150);
	}

	async function search() {
		const q = query.trim();
		if (!q) {
			results = [];
			hasSearched = false;
			return;
		}
		const { data } = await api.GET('/api/admin/library-search', { params: { query: { q } } });
		results = data?.results ?? [];
		hasSearched = true;
	}

	function onSubmit(event: SubmitEvent) {
		event.preventDefault();
		clearTimeout(searchTimer);
		search();
	}

	async function match(result: SearchResult) {
		if (adminMatchTarget.bookId == null || result.grimmory_id == null) return;
		matchingGrimmoryId = result.grimmory_id;
		matchError = null;
		try {
			unwrap(
				await api.POST('/api/admin/books/{book_id}/match', {
					params: { path: { book_id: adminMatchTarget.bookId } },
					body: { grimmory_id: result.grimmory_id }
				})
			);
			await invalidateAll();
			document.getElementById('admin-match-sheet')?.hidePopover();
		} catch (err) {
			matchError = err instanceof ApiError ? err.message : 'Could not reach the server — try again.';
		} finally {
			matchingGrimmoryId = null;
		}
	}
</script>

<div id="admin-match-sheet" popover class="sheet">
	<div class="sheet-handle"></div>
	<div class="sheet-title">MATCH "{adminMatchTarget.title}"</div>

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

	{#if matchError}<p class="error">{matchError}</p>{/if}

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
						disabled={matchingGrimmoryId != null || result.grimmory_id == null}
						onclick={() => match(result)}
					>
						Match
					</button>
				</li>
			{/each}
		</ul>
	{:else if hasSearched && query.trim()}
		<p class="empty-state">No results for "{query.trim()}".</p>
	{/if}
</div>
