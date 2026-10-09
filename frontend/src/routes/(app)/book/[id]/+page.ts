import { error } from '@sveltejs/kit';
import { api, unwrap, ApiError } from '$lib/api/client';
import { localDateString } from '$lib/utils/dates';

export const load = async ({ params, fetch }) => {
	const entryId = Number(params.id);
	if (!Number.isInteger(entryId)) error(404, 'Not found');
	try {
		const today = localDateString(new Date());
		// Fetched regardless of entry.owns_physical - cheap, and keeps this page's data-loading in
		// one place rather than a client-side fetch inside the sessions component itself. None of
		// the three calls depends on another's result, so they run concurrently - the session log
		// reads whatever's already in cached_reading_sessions at request time, which can in theory
		// lag one page load behind /api/book/{entry_id}'s own upsert of fresh Grimmory sessions;
		// an accepted staleness (settles on the next page view), not worth serializing every book
		// page load's latency to close.
		const [detailResult, physicalSessionsResult, sessionLogResult] = await Promise.all([
			api.GET('/api/book/{entry_id}', {
				params: { path: { entry_id: entryId }, query: { today } },
				fetch
			}),
			api.GET('/api/tbr/{entry_id}/physical-sessions', {
				params: { path: { entry_id: entryId } },
				fetch
			}),
			api.GET('/api/tbr/{entry_id}/sessions', {
				params: { path: { entry_id: entryId } },
				fetch
			})
		]);
		const detail = unwrap(detailResult);
		const physicalSessions = unwrap(physicalSessionsResult);
		const sessionLog = unwrap(sessionLogResult);
		return { detail, physicalSessions, sessionLog };
	} catch (err) {
		if (err instanceof ApiError && err.status === 404) error(404, 'Not found');
		throw err;
	}
};
