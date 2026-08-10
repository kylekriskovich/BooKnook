import { api, unwrap } from '$lib/api/client';

export const load = async ({ url, fetch }) => {
	const month = url.searchParams.get('month') ?? '';
	const calendar = unwrap(await api.GET('/api/calendar', { params: { query: { month } }, fetch }));
	return { calendar };
};
