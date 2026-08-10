import { error } from '@sveltejs/kit';
import { api, unwrap, ApiError } from '$lib/api/client';

export const load = async ({ params, fetch }) => {
	const entryId = Number(params.id);
	if (!Number.isInteger(entryId)) error(404, 'Not found');
	try {
		const detail = unwrap(
			await api.GET('/api/book/{entry_id}', { params: { path: { entry_id: entryId } }, fetch })
		);
		return { detail };
	} catch (err) {
		if (err instanceof ApiError && err.status === 404) error(404, 'Not found');
		throw err;
	}
};
