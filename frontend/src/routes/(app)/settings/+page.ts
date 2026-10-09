import { api, unwrap } from '$lib/api/client';
import { localDateString } from '$lib/utils/dates';

export const load = async ({ fetch }) => {
	const today = localDateString(new Date());
	const settings = unwrap(await api.GET('/api/settings', { params: { query: { today } }, fetch }));
	return { settings };
};
