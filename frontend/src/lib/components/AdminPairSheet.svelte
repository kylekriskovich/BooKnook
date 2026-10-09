<script lang="ts">
	import { api, unwrap } from '$lib/api/client';
	import { adminPairTarget } from '$lib/stores/adminPair.svelte';
	import AdminPickSheet from './AdminPickSheet.svelte';
	import type { components } from '$lib/api/schema';

	type SearchResult = components['schemas']['SearchResultOut'];

	// Strip a trailing "(Unabridged)"-style parenthetical so the prefill is a substring of the
	// ebook's title, matching search_library_catalog's LIKE %query%.
	function baseTitle(title: string): string {
		return title.replace(/\s*\([^)]*\)\s*$/, '').trim();
	}

	async function pair(audiobookGrimmoryId: number, result: SearchResult) {
		unwrap(
			await api.POST('/api/admin/audiobooks/{audiobook_grimmory_id}/pair', {
				params: { path: { audiobook_grimmory_id: audiobookGrimmoryId } },
				body: { ebook_grimmory_id: result.grimmory_id }
			})
		);
	}
</script>

<AdminPickSheet
	sheetId="admin-pair-sheet"
	heading={`PAIR "${adminPairTarget.title}" TO EBOOK`}
	targetId={adminPairTarget.id}
	prefillQuery={baseTitle(adminPairTarget.title)}
	buttonLabel="Pair"
	pick={pair}
/>
