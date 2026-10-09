import { api, unwrap } from '$lib/api/client';
import type { components } from '$lib/api/schema';

type TBREntry = components['schemas']['TBREntryOut'];

/**
 * Persists a new manual order for the "wanted" shelf, returning the server's fresh entries.
 * The local drag-and-drop state is already correct for ordering, but predicted_month depends on
 * order too, so the caller reconciles with this response rather than trusting the local reorder
 * alone (see ShelfList.svelte's handleFinalize).
 */
export async function persistWantedOrder(entryIds: number[]): Promise<TBREntry[]> {
	const data = unwrap(await api.POST('/api/shelf/wanted/reorder', { body: { entry_ids: entryIds } }));
	return data.entries;
}
