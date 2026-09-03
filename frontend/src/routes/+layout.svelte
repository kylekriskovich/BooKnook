<script lang="ts">
	import '../app.css';
	import { useRegisterSW } from 'virtual:pwa-register/svelte';
	import { beforeNavigate } from '$app/navigation';

	let { children } = $props();

	// autoUpdate (see vite.config.ts) means the new service worker activates on its own; nothing
	// needs to be done with needRefresh/offlineReady here, same "just works" posture as the old
	// hand-rolled service-worker.js had.
	useRegisterSW();

	// Close every open popover (BookModal, AccountSheet, AddSheet, the admin match/pair sheets)
	// before a client-side navigation starts - without this, e.g. AccountSheet's "Admin"/"My
	// Account" links navigate while the sheet stays visibly open, stacked over the new page, since
	// SvelteKit's router never unloads the document (which is what would otherwise make an "auto"
	// popover disappear on its own).
	beforeNavigate(() => {
		document.querySelectorAll(':popover-open').forEach((el) => (el as HTMLElement).hidePopover());
	});

	// Popover API's light-dismiss (closing an "auto" popover on an outside click) runs on
	// pointerdown, but the click event that follows re-targets against the now-current DOM - since
	// the popover and its ::backdrop are already gone by then, that click lands on whatever page
	// content is now exposed underneath and activates it too (a well-known Popover API gotcha, not
	// a bug specific to any one sheet). Remembering whether a popover was open at pointerdown, then
	// swallowing the resulting click in the capture phase - unless it actually originated inside a
	// popover (e.g. a "Keep"/Cancel button using popovertargetaction="hide", which must keep
	// working as its own legitimate interaction) - closes the popover without ever letting that
	// same click reach anything behind it.
	let popoverOpenAtPointerDown = false;

	function onPointerDownCapture() {
		popoverOpenAtPointerDown = !!document.querySelector(':popover-open');
	}

	function onClickCapture(event: MouseEvent) {
		const target = event.target as Element | null;
		if (popoverOpenAtPointerDown && !target?.closest('[popover]')) {
			event.preventDefault();
			event.stopPropagation();
			event.stopImmediatePropagation();
		}
	}
</script>

<svelte:window onpointerdowncapture={onPointerDownCapture} onclickcapture={onClickCapture} />

<svelte:head>
	<title>Book Knook</title>
</svelte:head>

{@render children()}
