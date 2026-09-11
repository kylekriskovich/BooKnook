import { error } from '@sveltejs/kit';
import { api, unwrap, ApiError } from '$lib/api/client';
import { localDateString } from '$lib/utils/dates';

export const load = async ({ params, fetch }) => {
	const entryId = Number(params.id);
	if (!Number.isInteger(entryId)) error(404, 'Not found');
	try {
		const today = localDateString(new Date());
		// Fetched regardless of entry.owns_physical - cheap, and keeps this page's data-loading in
		// one place rather than a client-side fetch inside the sessions component itself. Neither
		// call depends on the other's result, so they run concurrently.
		const [detailResult, physicalSessionsResult] = await Promise.all([
			api.GET('/api/book/{entry_id}', {
				params: { path: { entry_id: entryId }, query: { today } },
				fetch
			}),
			api.GET('/api/tbr/{entry_id}/physical-sessions', {
				params: { path: { entry_id: entryId } },
				fetch
			})
		]);
		const detail = unwrap(detailResult);
		const physicalSessions = unwrap(physicalSessionsResult);
		return { detail, physicalSessions };
	} catch (err) {
		if (err instanceof ApiError && err.status === 404) error(404, 'Not found');
		throw err;
	}
};
