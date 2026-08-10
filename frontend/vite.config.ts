import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';
import { VitePWA } from 'vite-plugin-pwa';

// Backend runs on a different port in dev (`uvicorn app.main:app --reload`, :8000) than the Vite
// dev server (:5173) — proxying keeps /api and /covers same-origin in dev too, so the signed
// session cookie (SameSite=Lax, set by FastAPI) behaves the same as it will in production, where
// FastAPI serves the built SPA and everything really is same-origin. See app/main.py's /covers
// StaticFiles mount and the /api/* routes.
const BACKEND_URL = process.env.BOOKNOOK_BACKEND_URL ?? 'http://localhost:8000';

export default defineConfig({
	plugins: [
		sveltekit({
			compilerOptions: {
				// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
				runes: ({ filename }) => (filename.split(/[/\\]/).includes('node_modules') ? undefined : true)
			},
			adapter: adapter({
				// fallback: 'index.html' = SPA mode — every route is served from one shell with
				// client-side routing, rather than each route needing to be prerenderable. Required
				// here since /shelf/[status] and /book/[id] are dynamic and their data only exists
				// behind an authenticated API call, not at build time — see src/routes/+layout.ts's
				// `ssr = false`, which this pairs with.
				fallback: 'index.html'
			})
		}),
		// Replaces the hand-rolled app/static/service-worker.js + manifest.json + register-sw.js
		// from the Jinja2/htmx app — same installable-PWA behavior (name/colors/icons carried over
		// unchanged below), but the precache list is generated from the actual build output instead
		// of a manually maintained PRECACHE_URLS array, so it can't go stale.
		VitePWA({
			registerType: 'autoUpdate',
			// SvelteKit's app.html isn't a plain Vite index.html, so the SW is registered manually
			// via useRegisterSW() in the root layout instead of the plugin's own injected script.
			injectRegister: false,
			manifest: {
				name: 'Book Knook',
				short_name: 'Book Knook',
				description: 'Shared to-be-read tracker for the Grimmory library',
				start_url: '/',
				scope: '/',
				display: 'standalone',
				background_color: '#12181c',
				theme_color: '#12181c',
				icons: [
					{ src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
					{ src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
					{
						src: '/icons/icon-maskable-192.png',
						sizes: '192x192',
						type: 'image/png',
						purpose: 'maskable'
					},
					{
						src: '/icons/icon-maskable-512.png',
						sizes: '512x512',
						type: 'image/png',
						purpose: 'maskable'
					}
				]
			},
			workbox: {
				// Same "static assets only, always network for everything else" split the
				// hand-rolled service worker had — /api and /covers must never be served stale
				// (shelves/covers can't go stale, and covers must never leak between logged-in
				// users sharing a device), so both are excluded from the precache/navigation
				// fallback and pinned to NetworkOnly.
				navigateFallbackDenylist: [/^\/api\//, /^\/covers\//],
				runtimeCaching: [
					{
						urlPattern: /^\/covers\//,
						handler: 'NetworkOnly'
					},
					{
						urlPattern: /^\/api\//,
						handler: 'NetworkOnly'
					}
				]
			}
		})
	],
	server: {
		proxy: {
			'/api': BACKEND_URL,
			'/covers': BACKEND_URL,
			'/health': BACKEND_URL
		}
	}
});
