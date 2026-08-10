import { api, unwrap } from '$lib/api/client';

export const load = async ({ fetch }) => {
	const settings = unwrap(await api.GET('/api/admin/settings', { fetch }));
	return { settings };
};
