<script lang="ts">
	import { api, unwrap } from '$lib/api/client';
	import { auth } from '$lib/stores/auth.svelte';
	import SpiceSection from '$lib/components/SpiceSection.svelte';
	import ShelfSyncSection from '$lib/components/ShelfSyncSection.svelte';

	let { data } = $props();

	let syncPassword = $state('');
	let syncing = $state(false);
	let syncError = $state<string | null>(null);
	let synced = $state(false);

	async function sync(event: SubmitEvent) {
		event.preventDefault();
		syncing = true;
		synced = false;
		try {
			const result = unwrap(await api.POST('/api/settings/sync', { body: { password: syncPassword } }));
			syncError = result.error ?? null;
			synced = result.error == null;
			syncPassword = '';
		} finally {
			syncing = false;
		}
	}

	// This page has no route param, so a fresh instance (and a fresh read of `data`) is created
	// every time it's navigated to — capturing only the initial value here is fine.
	// svelte-ignore state_referenced_locally
	let goalTarget = $state(data.settings.goal ? String(data.settings.goal.target_count) : '');
	let savingGoal = $state(false);
	// svelte-ignore state_referenced_locally
	let goal = $state(data.settings.goal);

	async function saveGoal(event: SubmitEvent) {
		event.preventDefault();
		const target = Number(goalTarget);
		if (!target || target < 1) return;
		savingGoal = true;
		try {
			goal = unwrap(await api.POST('/api/settings/goal', { body: { target_count: target } }));
		} finally {
			savingGoal = false;
		}
	}
</script>

<svelte:head>
	<title>Settings — Book Knook</title>
</svelte:head>

<div class="list-header">
	<span class="list-title">Settings</span>
</div>

<div class="settings-section">
	<div class="settings-section-title">Reading status</div>
	<p class="empty-state">Updates automatically. Sync now if you don't want to wait.</p>
	{#if data.settings.has_grimmory_session}
		<form onsubmit={sync}>
			<button type="submit" class="btn btn-accent" disabled={syncing}>Sync now</button>
		</form>
	{:else}
		<form class="settings-form" onsubmit={sync}>
			<label>
				Grimmory password
				<input type="password" autocomplete="current-password" required bind:value={syncPassword} />
			</label>
			<button type="submit" class="btn btn-accent" disabled={syncing}>Sync now</button>
		</form>
	{/if}
	<div id="sync-result">
		{#if syncError === 'reconnect_needed'}
			<p class="error">Your Grimmory session has expired — enter your password to reconnect.</p>
			<form class="settings-form" onsubmit={sync}>
				<label>
					Grimmory password
					<input type="password" autocomplete="current-password" required bind:value={syncPassword} />
				</label>
				<button type="submit" class="btn btn-accent" disabled={syncing}>Reconnect &amp; sync</button>
			</form>
		{:else if syncError}
			<p class="error">{syncError}</p>
		{:else if synced}
			<p class="sync-success">Synced — reading status updated.</p>
		{/if}
	</div>
</div>

{#if data.settings.has_grimmory_session}
	<ShelfSyncSection
		wantToReadShelfId={data.settings.want_to_read_shelf_id ?? null}
		syncToDeviceEnabled={data.settings.sync_to_device_enabled}
		syncToDeviceShelfId={data.settings.sync_to_device_shelf_id ?? null}
	/>
{/if}

<SpiceSection grimmoryAdminConfigured={data.settings.grimmory_admin_configured} spiceLabels={data.settings.spice_labels} />

<div class="settings-section">
	<div class="settings-section-title">Reading goal</div>
	<div class="settings-list">
		<div class="settings-row">
			<span class="settings-row-label">{data.settings.goal_year} target</span>
			<span class="settings-row-value">{goal ? `${goal.target_count} books` : 'Not set'}</span>
		</div>
	</div>
	<form class="settings-form" onsubmit={saveGoal}>
		<label>
			Books to finish in {data.settings.goal_year}
			<input type="number" min="1" placeholder="e.g. 20" bind:value={goalTarget} />
		</label>
		<button type="submit" class="btn btn-accent" disabled={savingGoal}>{goal ? 'Update goal' : 'Set goal'}</button>
	</form>
</div>

{#if auth.user?.is_admin}
	<div class="settings-section">
		<div class="settings-section-title">Admin</div>
		<div class="settings-list">
			<a href="/admin" class="settings-row">
				<span class="settings-row-label">All TBR requests</span>
				<span class="settings-row-chevron"></span>
			</a>
			<a href="/admin/settings" class="settings-row">
				<span class="settings-row-label">Library settings</span>
				<span class="settings-row-chevron"></span>
			</a>
		</div>
	</div>
{/if}
