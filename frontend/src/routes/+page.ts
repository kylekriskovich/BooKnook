import { redirect } from '@sveltejs/kit';
import { auth } from '$lib/stores/auth.svelte';

// Client-side equivalent of GET / in app/main.py: show the login page when there's no session,
// or skip straight to /home when there is one. The (auth)/login and (app)/* layouts hold the
// actual guard logic — this route only exists to give "/" somewhere to send the browser.
export const load = async () => {
	const user = auth.user === undefined ? await auth.refresh() : auth.user;
	redirect(307, user ? '/home' : '/login');
};
