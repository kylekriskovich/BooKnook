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

	// Popover light-dismiss fires on pointerdown, but the click that follows re-targets after
	// the popover is gone and hits whatever's underneath. Swallow that click unless it
	// originated inside a popover (e.g. a popovertargetaction="hide" button) - or is the browser's
	// own forwarded click from a <label> inside a popover to its associated control, which for
	// this app's CSS-only view toggles (app.css's #view-spine:checked etc.) intentionally lives
	// outside the popover - swallowing that click meant the toggle could never fire once a popover
	// was open.
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
