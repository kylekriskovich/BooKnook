import { auth } from '$lib/stores/auth.svelte';

// /admin* has no in-app auth, gated at the reverse proxy instead — same posture as GET /admin in
// app/main.py. Unlike (app)/+layout.ts this never redirects; it just opportunistically loads the
// session (if any) so AppChrome can decide whether to show the nav/account-sheet.
export const load = async () => {
	if (auth.user === undefined) await auth.refresh();
};
