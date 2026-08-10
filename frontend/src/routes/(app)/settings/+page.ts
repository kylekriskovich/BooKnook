import { api, unwrap } from '$lib/api/client';

export const load = async ({ fetch }) => {
	const settings = unwrap(await api.GET('/api/settings', { fetch }));
	return { settings };
};
