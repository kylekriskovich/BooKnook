// SPA mode: adapter-static's fallback page has no server to run load functions against, so every
// route in this app is client-rendered — see vite.config.ts's adapter({ fallback: 'index.html' }).
export const ssr = false;
