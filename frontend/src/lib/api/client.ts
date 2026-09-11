import createClient from 'openapi-fetch';
import type { paths } from './schema';

// baseUrl '' = same-origin: the dev server proxies /api to the FastAPI backend (see
// vite.config.ts's server.proxy), and in production FastAPI serves this app's build output
// directly, so /api is always same-origin — no CORS, and the signed session cookie
// (httponly, SameSite=Lax — see app/main.py's COOKIE_NAME) rides along automatically.
export const api = createClient<paths>({ baseUrl: '', credentials: 'same-origin' });

export class ApiError extends Error {
	status: number;

	constructor(status: number, detail: string) {
		super(detail);
		this.name = 'ApiError';
		this.status = status;
	}
}

// Capturing the whole result as R then extracting `data` from it is what makes TS resolve this —
// inferring a bare `data?: T` param directly fails through openapi-fetch's conditional types.
type UnwrappedData<R> = R extends { data?: infer D } ? NonNullable<D> : never;

/** Unwraps an openapi-fetch result, throwing an ApiError (FastAPI's `detail` message) instead of
 * making every call site check `error`/`data`. */
export function unwrap<R extends { data?: unknown; error?: unknown; response: Response }>(
	result: R
): UnwrappedData<R> {
	if (result.error !== undefined) {
		const detail =
			typeof result.error === 'object' && result.error !== null && 'detail' in result.error
				? String((result.error as { detail: unknown }).detail)
				: result.response.statusText;
		throw new ApiError(result.response.status, detail);
	}
	return result.data as UnwrappedData<R>;
}

/** User-facing message for a caught unwrap() error: the API's own detail, or a generic fallback
 * for a network failure. */
export function describeError(err: unknown): string {
	return err instanceof ApiError ? err.message : 'Could not reach the server — try again.';
}
