<script lang="ts">
	import { api } from '$lib/api/client';
	import { auth } from '$lib/stores/auth.svelte';
	import { afterNavigate, goto } from '$app/navigation';

	let sheet: HTMLDivElement;

	// The rows below are <a href> links that SvelteKit intercepts for client-side navigation, so
	// the browser never unloads the page and the native popover's light-dismiss never fires. Force
	// it closed on every navigation instead (a no-op if it's already closed).
	afterNavigate(() => {
		sheet?.hidePopover();
	});

	async function logout() {
		await api.POST('/api/logout');
		auth.set(null);
		await goto('/login');
	}
</script>

<div id="account-sheet" popover class="sheet" bind:this={sheet}>
	<div class="sheet-handle"></div>
	<div class="sheet-title">ACCOUNT</div>
	<div class="settings-list">
		<div class="settings-row">
			<span class="settings-row-label">Logged in as</span>
			<span class="settings-row-value">{auth.user?.name ?? ''}</span>
		</div>
		<a href="/settings" class="settings-row">
			<span class="settings-row-label">My Account</span>
			<span class="settings-row-chevron"></span>
		</a>
		{#if auth.user?.is_admin}
			<a href="/admin" class="settings-row">
				<span class="settings-row-label">Admin</span>
				<span class="settings-row-chevron"></span>
			</a>
		{/if}
	</div>

	<div class="sheet-title account-sheet-subtitle">DISPLAY</div>
	<div class="view-tabs">
		<label for="view-cover" class="view-tab">COVER</label>
		<label for="view-spine" class="view-tab">SPINE</label>
	</div>

	<div class="settings-list account-sheet-logout">
		<button type="button" class="settings-row settings-row-danger" onclick={logout}>Log out</button>
	</div>
</div>
