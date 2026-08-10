<script lang="ts">
	import { api } from '$lib/api/client';
	import { auth } from '$lib/stores/auth.svelte';
	import { goto } from '$app/navigation';

	async function logout() {
		await api.POST('/api/logout');
		auth.set(null);
		await goto('/login');
	}
</script>

<div id="account-sheet" popover class="sheet">
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
