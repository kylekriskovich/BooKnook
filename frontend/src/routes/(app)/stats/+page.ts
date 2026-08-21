import { api, unwrap } from '$lib/api/client';
import { localDateString } from '$lib/utils/dates';

export const load = async ({ fetch }) => {
	const today = localDateString(new Date());
	const stats = unwrap(await api.GET('/api/stats', { params: { query: { today } }, fetch }));
	return { stats };
};
