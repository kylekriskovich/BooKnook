import { redirect } from '@sveltejs/kit';
import { auth } from '$lib/stores/auth.svelte';
import type { Me } from '$lib/stores/auth.svelte';

// Client-side equivalent of app/main.py's require_user dependency — every page under this group
// needs a session, or gets sent to /login (matching the 401-vs-redirect split the API itself
// draws: the API returns 401 JSON, this is the browser-facing redirect on top of that).
export const load = async (): Promise<{ user: Me }> => {
	const user = auth.user === undefined ? await auth.refresh() : auth.user;
	if (!user) redirect(307, '/login');
	return { user };
};
