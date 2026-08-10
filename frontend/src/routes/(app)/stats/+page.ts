import { api, unwrap } from '$lib/api/client';

export const load = async ({ fetch }) => {
	const stats = unwrap(await api.GET('/api/stats', { fetch }));
	return { stats };
};
