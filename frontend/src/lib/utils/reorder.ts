import { api } from '$lib/api/client';

/**
 * Persists a new manual order for the "wanted" shelf. Fire-and-forget (see ShelfList.svelte) —
 * the local drag-and-drop state is already correct, so this just needs the server to agree.
 */
export async function persistWantedOrder(entryIds: number[]): Promise<void> {
	await api.POST('/api/shelf/wanted/reorder', { body: { entry_ids: entryIds } });
}
