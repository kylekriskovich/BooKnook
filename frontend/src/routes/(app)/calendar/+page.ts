import { api, unwrap } from '$lib/api/client';
import { localDateString } from '$lib/utils/dates';

export const load = async ({ url, fetch }) => {
	const month = url.searchParams.get('month') ?? '';
	const today = localDateString(new Date());
	const calendar = unwrap(
		await api.GET('/api/calendar', { params: { query: { month, today } }, fetch })
	);
	return { calendar };
};
