import { redirect } from '@sveltejs/kit';
import { auth } from '$lib/stores/auth.svelte';

// Only /login lives in this group. An already-logged-in user hitting it (e.g. a bookmark, or
// browser back after logging in) goes straight to /home instead of seeing the form again.
export const load = async () => {
	const user = auth.user === undefined ? await auth.refresh() : auth.user;
	if (user) redirect(307, '/home');
};
