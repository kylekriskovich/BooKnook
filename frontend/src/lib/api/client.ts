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

// Pulls the success-case `data` type off an openapi-fetch FetchResponse<...> result — inferring
// it this way (capture the whole result as R, then extract from R) instead of trying to infer a
// bare `data?: T` type parameter directly is what makes TS actually resolve it; the latter fails
// to infer through openapi-fetch's conditional response types and silently falls back to
// `unknown`.
type UnwrappedData<R> = R extends { data?: infer D } ? NonNullable<D> : never;

/**
 * Unwraps an openapi-fetch result, throwing an ApiError (with the FastAPI HTTPException's
 * `detail` message) on failure instead of making every call site check `error`/`data` — most
 * callers just want the data or a thrown error a top-level handler can catch.
 */
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
