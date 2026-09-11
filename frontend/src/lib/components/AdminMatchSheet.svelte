<script lang="ts">
	import { api, unwrap } from '$lib/api/client';
	import { adminMatchTarget } from '$lib/stores/adminMatch.svelte';
	import AdminPickSheet from './AdminPickSheet.svelte';
	import type { components } from '$lib/api/schema';

	type SearchResult = components['schemas']['SearchResultOut'];

	async function match(bookId: number, result: SearchResult) {
		unwrap(
			await api.POST('/api/admin/books/{book_id}/match', {
				params: { path: { book_id: bookId } },
				body: { grimmory_id: result.grimmory_id }
			})
		);
	}
</script>

<AdminPickSheet
	sheetId="admin-match-sheet"
	heading={`MATCH "${adminMatchTarget.title}"`}
	targetId={adminMatchTarget.id}
	buttonLabel="Match"
	pick={match}
/>
