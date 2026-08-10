import { api } from '$lib/api/client';

/**
 * Persists a new manual order for the "wanted" shelf. Fire-and-forget from the caller's
 * perspective (see ShelfRow.svelte / ShelfList.svelte) — the local drag-and-drop state is already
 * the correct order the instant the drop happens, so this just needs to make the server agree
 * before the next real reload; no need to await/re-render on it.
 */
export async function persistWantedOrder(entryIds: number[]): Promise<void> {
	await api.POST('/api/shelf/wanted/reorder', { body: { entry_ids: entryIds } });
}
