import { api, unwrap } from '$lib/api/client';
import { localDateString } from '$lib/utils/dates';

export const load = async ({ fetch }) => {
	const today = localDateString(new Date());
	const home = unwrap(await api.GET('/api/home', { params: { query: { today } }, fetch }));
	return { shelves: home.shelves };
};
