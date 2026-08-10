import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';

export type Me = components['schemas']['MeOut'];

/**
 * The current session, client-side equivalent of app/main.py's `current_user`/`is_admin_user`
 * Jinja2 globals. `user` is `undefined` until the first `/api/me` call resolves (so callers can
 * tell "not checked yet" from "checked, and there's no session"), then either the session or
 * `null`. A plain module-level class instance (not a context) since there is exactly one session
 * for the whole app — every route's `+layout.ts` guard reads/writes the same instance.
 */
class AuthStore {
	user: Me | null | undefined = $state(undefined);

	async refresh(): Promise<Me | null> {
		const { data } = await api.GET('/api/me');
		this.user = data ?? null;
		return this.user;
	}

	set(user: Me | null) {
		this.user = user;
	}
}

export const auth = new AuthStore();
