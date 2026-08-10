import { api, unwrap } from '$lib/api/client';

export const load = async ({ fetch }) => {
	const admin = unwrap(await api.GET('/api/admin', { fetch }));
	return { admin };
};
