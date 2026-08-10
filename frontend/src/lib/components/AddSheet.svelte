<script lang="ts">
	import { api } from '$lib/api/client';
	import type { components } from '$lib/api/schema';
	import ResultCard from './ResultCard.svelte';

	type SearchResult = components['schemas']['SearchResultOut'];

	let query = $state('');
	let libraryResults = $state<SearchResult[]>([]);

	let results = $state<SearchResult[]>([]);
	let searchError = $state(false);
	let searchErrorMessage = $state<string | null>(null);
	let showMore = $state(false);
	let hasSearched = $state(false);

	let moreResults = $state<SearchResult[] | null>(null);
	let moreError = $state(false);

	let libraryTimer: ReturnType<typeof setTimeout>;
	let searchTimer: ReturnType<typeof setTimeout>;

	// Two independent debounces mirroring app/templates/_add_sheet.html's two htmx triggers: the
	// local catalog match (GET /api/search/library) is instant/no-network and fires fast (150ms),
	// while the external metadata search (GET /api/search) is slower and fires on a longer pause
	// (1200ms) or on submit.
	function onInput() {
		clearTimeout(libraryTimer);
		libraryTimer = setTimeout(fetchLibrary, 150);
		clearTimeout(searchTimer);
		searchTimer = setTimeout(fetchSearch, 1200);
	}

	async function fetchLibrary() {
		const q = query.trim();
		if (!q) {
			libraryResults = [];
			return;
		}
		const { data } = await api.GET('/api/search/library', { params: { query: { q } } });
		libraryResults = data?.results ?? [];
	}

	async function fetchSearch() {
		const q = query.trim();
		moreResults = null;
		if (!q) {
			results = [];
			hasSearched = false;
			showMore = false;
			return;
		}
		const { data } = await api.GET('/api/search', { params: { query: { q } } });
		results = data?.results ?? [];
		searchError = data?.error ?? false;
		searchErrorMessage = data?.error_message ?? null;
		showMore = data?.show_more ?? false;
		hasSearched = true;
	}

	function onSubmit(event: SubmitEvent) {
		event.preventDefault();
		clearTimeout(searchTimer);
		fetchSearch();
	}

	async function fetchMore() {
		const q = query.trim();
		const { data } = await api.GET('/api/search/more', { params: { query: { q } } });
		moreResults = data?.results ?? [];
		moreError = data?.error ?? false;
	}
</script>

<div id="add-sheet" popover class="sheet">
	<div class="sheet-handle"></div>
	<div class="sheet-title">ADD TO TBR</div>

	<form class="search-form" onsubmit={onSubmit}>
		<input
			id="search-input"
			type="search"
			placeholder="Search by title or author"
			autocomplete="off"
			bind:value={query}
			oninput={onInput}
		/>
		<button type="submit">Search</button>
	</form>

	{#if libraryResults.length}
		<div id="library-results">
			<div class="settings-section-title">In your library</div>
			<ul class="search-results-list">
				{#each libraryResults as result (result.isbn ?? result.title)}
					<ResultCard {result} />
				{/each}
			</ul>
		</div>
	{/if}

	<div id="search-results">
		{#if searchError}
			<p class="error">{searchErrorMessage ?? 'Search failed — try again.'}</p>
		{:else if results.length}
			<ul class="search-results-list">
				{#each results as result (result.isbn ?? result.title)}
					<ResultCard {result} />
				{/each}
			</ul>
		{:else if hasSearched && query.trim()}
			<p class="empty-state">No results for "{query.trim()}".</p>
		{/if}

		{#if showMore}
			{#if moreResults === null}
				<button type="button" class="btn btn-ghost" onclick={fetchMore}>More search results</button>
			{:else if moreError}
				<p class="error">Open Library search failed — try again.</p>
			{:else if moreResults.length}
				<ul class="search-results-list">
					{#each moreResults as result (result.isbn ?? result.title)}
						<ResultCard {result} />
					{/each}
				</ul>
			{:else}
				<p class="empty-state">No further results.</p>
			{/if}
		{/if}
	</div>
</div>
