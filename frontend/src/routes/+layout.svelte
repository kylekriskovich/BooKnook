<script lang="ts">
	import '../app.css';
	import { useRegisterSW } from 'virtual:pwa-register/svelte';

	let { children } = $props();

	// autoUpdate (see vite.config.ts) means the new service worker activates on its own; nothing
	// needs to be done with needRefresh/offlineReady here, same "just works" posture as the old
	// hand-rolled service-worker.js had.
	useRegisterSW();
</script>

<svelte:head>
	<title>Book Knook</title>
</svelte:head>

{@render children()}
