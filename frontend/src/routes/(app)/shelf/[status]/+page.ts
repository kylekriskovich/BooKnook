import { error } from '@sveltejs/kit';
import { api, unwrap, ApiError } from '$lib/api/client';
import { localDateString } from '$lib/utils/dates';

export const load = async ({ params, fetch }) => {
	try {
		const today = localDateString(new Date());
		const shelf = unwrap(
			await api.GET('/api/shelf/{status}', {
				params: { path: { status: params.status }, query: { today } },
				fetch
			})
		);
		return { shelf };
	} catch (err) {
		if (err instanceof ApiError && err.status === 404) error(404, 'Unknown shelf');
		throw err;
	}
};
