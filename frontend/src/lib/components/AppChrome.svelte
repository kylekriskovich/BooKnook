<script lang="ts">
	import { api } from '$lib/api/client';
	import { auth } from '$lib/stores/auth.svelte';
	import AccountSheet from './AccountSheet.svelte';

	let { children } = $props();

	async function setView(view: 'spine' | 'cover') {
		if (!auth.user || auth.user.view_preference === view) return;
		auth.user.view_preference = view;
		await api.POST('/api/preferences/view', { body: { view } });
	}
</script>

<header class="site-header">
	<a class="brand" href="/home">Book Knook</a>
</header>

{#if auth.user}
	<!-- Pure-CSS spine/cover toggle: home's ShelfRow renders both blocks, and
	     #view-spine:checked ~ main #shelves .shelf-spine (see app.css) shows the right one — same
	     mechanism the Jinja2/htmx app used, just posting the choice via fetch instead of hx-post. -->
	<input
		type="radio"
		name="view"
		id="view-spine"
		class="view-radio"
		value="spine"
		checked={auth.user.view_preference !== 'cover'}
		onchange={() => setView('spine')}
	/>
	<input
		type="radio"
		name="view"
		id="view-cover"
		class="view-radio"
		value="cover"
		checked={auth.user.view_preference === 'cover'}
		onchange={() => setView('cover')}
	/>
{/if}

<main>
	{@render children()}
</main>

{#if auth.user}
	<nav class="bottom-nav">
		<a href="/home" class="navicon navicon-home" title="Home">
			<svg width="24" height="24" viewBox="0 -960 960 960" fill="currentColor" aria-hidden="true">
				<path
					d="M240-200h120v-240h240v240h120v-360L480-740 240-560v360Zm-80 80v-480l320-240 320 240v480H520v-240h-80v240H160Zm320-350Z"
				/>
			</svg>
		</a>
		<a href="/stats" class="navicon navicon-bars" title="Stats">
			<svg width="24" height="24" viewBox="0 -960 960 960" fill="currentColor" aria-hidden="true">
				<path
					d="M280-600v-80h560v80H280Zm0 160v-80h560v80H280Zm0 160v-80h560v80H280ZM160-600q-17 0-28.5-11.5T120-640q0-17 11.5-28.5T160-680q17 0 28.5 11.5T200-640q0 17-11.5 28.5T160-600Zm0 160q-17 0-28.5-11.5T120-480q0-17 11.5-28.5T160-520q17 0 28.5 11.5T200-480q0 17-11.5 28.5T160-440Zm0 160q-17 0-28.5-11.5T120-320q0-17 11.5-28.5T160-360q17 0 28.5 11.5T200-320q0 17-11.5 28.5T160-280Z"
				/>
			</svg>
		</a>
		<button type="button" popovertarget="add-sheet" class="fab" title="Add a book">
			<svg class="fab-plus" width="24" height="24" viewBox="0 -960 960 960" fill="currentColor" aria-hidden="true">
				<path d="M440-440H200v-80h240v-240h80v240h240v80H520v240h-80v-240Z" />
			</svg>
		</button>
		<a href="/calendar" class="navicon navicon-calendar" title="Reading Calendar">
			<svg width="24" height="24" viewBox="0 -960 960 960" fill="currentColor" aria-hidden="true">
				<path
					d="M200-80q-33 0-56.5-23.5T120-160v-560q0-33 23.5-56.5T200-800h40v-80h80v80h320v-80h80v80h40q33 0 56.5 23.5T840-720v560q0 33-23.5 56.5T760-80H200Zm0-80h560v-400H200v400Zm0-480h560v-80H200v80Zm0 0v-80 80Zm280 240q-17 0-28.5-11.5T440-440q0-17 11.5-28.5T480-480q17 0 28.5 11.5T520-440q0 17-11.5 28.5T480-400Zm-188.5-11.5Q280-423 280-440t11.5-28.5Q303-480 320-480t28.5 11.5Q360-457 360-440t-11.5 28.5Q337-400 320-400t-28.5-11.5ZM640-400q-17 0-28.5-11.5T600-440q0-17 11.5-28.5T640-480q17 0 28.5 11.5T680-440q0 17-11.5 28.5T640-400ZM480-240q-17 0-28.5-11.5T440-280q0-17 11.5-28.5T480-320q17 0 28.5 11.5T520-280q0 17-11.5 28.5T480-240Zm-188.5-11.5Q280-263 280-280t11.5-28.5Q303-320 320-320t28.5 11.5Q360-297 360-280t-11.5 28.5Q337-240 320-240t-28.5-11.5ZM640-240q-17 0-28.5-11.5T600-280q0-17 11.5-28.5T640-320q17 0 28.5 11.5T680-280q0 17-11.5 28.5T640-240Z"
				/>
			</svg>
		</a>
		<button type="button" popovertarget="account-sheet" class="navicon navicon-settings" title="My Account">
			<svg width="24" height="24" viewBox="0 -960 960 960" fill="currentColor" aria-hidden="true">
				<path
					d="m370-80-16-128q-13-5-24.5-12T307-235l-119 50L78-375l103-78q-1-7-1-13.5v-27q0-6.5 1-13.5L78-585l110-190 119 50q11-8 23-15t24-12l16-128h220l16 128q13 5 24.5 12t22.5 15l119-50 110 190-103 78q1 7 1 13.5v27q0 6.5-2 13.5l103 78-110 190-118-50q-11 8-23 15t-24 12L590-80H370Zm70-80h79l14-106q31-8 57.5-23.5T639-327l99 41 39-68-86-65q5-14 7-29.5t2-31.5q0-16-2-31.5t-7-29.5l86-65-39-68-99 42q-22-23-48.5-38.5T533-694l-13-106h-79l-14 106q-31 8-57.5 23.5T321-633l-99-41-39 68 86 64q-5 15-7 30t-2 32q0 16 2 31t7 30l-86 65 39 68 99-42q22 23 48.5 38.5T427-266l13 106Zm42-180q58 0 99-41t41-99q0-58-41-99t-99-41q-59 0-99.5 41T342-480q0 58 40.5 99t99.5 41Zm-2-140Z"
				/>
			</svg>
		</button>
	</nav>
	<AccountSheet />
{/if}
