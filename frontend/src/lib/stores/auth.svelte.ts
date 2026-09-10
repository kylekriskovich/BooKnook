import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';

export type Me = components['schemas']['MeOut'];

/**
 * The current session. `user` is `undefined` until the first `/api/me` call resolves (so callers
 * can tell "not checked yet" from "checked, no session"), then the session or `null`. A singleton
 * module instance, not a context — every route's `+layout.ts` guard reads/writes the same one.
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
