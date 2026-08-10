import { api, unwrap } from '$lib/api/client';

export const load = async ({ fetch }) => {
	const home = unwrap(await api.GET('/api/home', { fetch }));
	return { shelves: home.shelves };
};
