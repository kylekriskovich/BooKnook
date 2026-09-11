<script lang="ts">
	import '../app.css';
	import { useRegisterSW } from 'virtual:pwa-register/svelte';
	import { beforeNavigate } from '$app/navigation';

	let { children } = $props();

	// autoUpdate (see vite.config.ts) activates the new service worker automatically.
	useRegisterSW();

	// SvelteKit's router never unloads the document, so an open popover would stay stacked
	// over the new page after a client-side navigation unless closed explicitly.
	beforeNavigate(() => {
		document.querySelectorAll(':popover-open').forEach((el) => (el as HTMLElement).hidePopover());
	});

	// Popover light-dismiss fires on pointerdown; the click that follows re-targets onto whatever's
	// now underneath. Swallow it unless it started inside a popover, or is a browser-forwarded
	// label click reaching a CSS-toggle radio that intentionally lives outside the popover.
	let popoverOpenAtPointerDown = false;

	function onPointerDownCapture() {
		popoverOpenAtPointerDown = !!document.querySelector(':popover-open');
	}

	function onClickCapture(event: MouseEvent) {
		const target = event.target as Element | null;
		if (!popoverOpenAtPointerDown || target?.closest('[popover]')) return;
		if (target instanceof HTMLInputElement && [...(target.labels ?? [])].some((label) => label.closest('[popover]'))) {
			return;
		}
		event.preventDefault();
		event.stopPropagation();
		event.stopImmediatePropagation();
	}
</script>

<svelte:window onpointerdowncapture={onPointerDownCapture} onclickcapture={onClickCapture} />

<svelte:head>
	<title>Book Knook</title>
</svelte:head>

{@render children()}
