import { error } from '@sveltejs/kit';
import { api, unwrap, ApiError } from '$lib/api/client';

export const load = async ({ params, fetch }) => {
	try {
		const shelf = unwrap(
			await api.GET('/api/shelf/{status}', { params: { path: { status: params.status } }, fetch })
		);
		return { shelf };
	} catch (err) {
		if (err instanceof ApiError && err.status === 404) error(404, 'Unknown shelf');
		throw err;
	}
};
