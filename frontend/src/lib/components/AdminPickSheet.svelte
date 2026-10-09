<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { api, describeError, unwrap } from '$lib/api/client';
	import type { components } from '$lib/api/schema';

	type SearchResult = components['schemas']['SearchResultOut'];

	let {
		sheetId,
		heading,
		targetId,
		prefillQuery = null,
		buttonLabel,
		pick
	}: {
		sheetId: string;
		heading: string;
		targetId: number | null;
		prefillQuery?: string | null;
		buttonLabel: string;
		pick: (targetId: number, result: SearchResult) => Promise<void>;
	} = $props();

	let query = $state('');
	let results = $state<SearchResult[]>([]);
	let hasSearched = $state(false);
	let pickingGrimmoryId = $state<number | null>(null);
	let pickError = $state<string | null>(null);
	let searchError = $state<string | null>(null);

	let searchTimer: ReturnType<typeof setTimeout> | undefined = undefined;
	// Bumped on every new search and on each target switch; a resolving request only applies its
	// results if it's still the most recent one issued (issue #14) - otherwise a slow response for
	// an old query/target can land after a newer one and overwrite what's currently showing.
	let searchGeneration = 0;

	// Reset each time a different row is targeted; prefillQuery (if given) also runs a search.
	// Uses a local `initial` rather than reading `query` back, so typing (which also writes
	// `query` via bind:value) doesn't retrigger this effect and stomp what was just typed.
	$effect(() => {
		void targetId;
		searchGeneration++;
		const initial = prefillQuery ?? '';
		query = initial;
		results = [];
		hasSearched = false;
		pickError = null;
		searchError = null;
		if (initial) search(initial);
	});

	function onInput() {
		clearTimeout(searchTimer);
		searchTimer = setTimeout(() => search(query), 150);
	}

	async function search(q: string) {
		q = q.trim();
		const generation = ++searchGeneration;
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
			if (generation !== searchGeneration) return;
			results = data.results;
			searchError = null;
		} catch (err) {
			if (generation !== searchGeneration) return;
			results = [];
			searchError = describeError(err);
		} finally {
			if (generation === searchGeneration) hasSearched = true;
		}
	}

	function onSubmit(event: SubmitEvent) {
		event.preventDefault();
		clearTimeout(searchTimer);
		search(query);
	}

	async function doPick(result: SearchResult) {
		if (targetId == null || result.grimmory_id == null) return;
		pickingGrimmoryId = result.grimmory_id;
		pickError = null;
		try {
			await pick(targetId, result);
			await invalidateAll();
			document.getElementById(sheetId)?.hidePopover();
		} catch (err) {
			pickError = describeError(err);
		} finally {
			pickingGrimmoryId = null;
		}
	}
</script>

<div id={sheetId} popover class="sheet">
	<div class="sheet-handle"></div>
	<div class="sheet-title">{heading}</div>

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

	{#if pickError}<p class="error">{pickError}</p>{/if}
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
						disabled={pickingGrimmoryId != null || result.grimmory_id == null}
						onclick={() => doPick(result)}
					>
						{buttonLabel}
					</button>
				</li>
			{/each}
		</ul>
	{:else if hasSearched && query.trim() && !searchError}
		<p class="empty-state">No results for "{query.trim()}".</p>
	{/if}
</div>
