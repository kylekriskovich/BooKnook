<script lang="ts">
	import { api, unwrap } from '$lib/api/client';

	let { data } = $props();
	// This page has no route param, so a fresh instance (and a fresh read of `data`) is created
	// every time it's navigated to — capturing only initial values below is fine, and `settings`
	// is deliberately re-assigned wholesale after each save rather than patched field-by-field.
	// svelte-ignore state_referenced_locally
	let settings = $state(data.settings);

	// svelte-ignore state_referenced_locally
	let libraryBaseUrl = $state(settings.library_settings?.base_url ?? '');
	// svelte-ignore state_referenced_locally
	let libraryUsername = $state(settings.library_settings?.username ?? '');
	let libraryPassword = $state('');
	// svelte-ignore state_referenced_locally
	let syncIntervalMinutes = $state(
		settings.library_settings?.sync_interval_minutes ?? settings.default_sync_interval_minutes
	);
	let savingLibrary = $state(false);

	async function saveLibrary(event: SubmitEvent) {
		event.preventDefault();
		savingLibrary = true;
		try {
			settings = unwrap(
				await api.POST('/api/admin/settings', {
					body: {
						base_url: libraryBaseUrl,
						username: libraryUsername,
						password: libraryPassword,
						sync_interval_minutes: syncIntervalMinutes
					}
				})
			);
			libraryPassword = '';
		} finally {
			savingLibrary = false;
		}
	}

	// svelte-ignore state_referenced_locally
	let adminUsername = $state(settings.grimmory_admin_settings?.username ?? '');
	let adminPassword = $state('');
	let savingAdmin = $state(false);

	async function saveAdmin(event: SubmitEvent) {
		event.preventDefault();
		savingAdmin = true;
		try {
			settings = unwrap(
				await api.POST('/api/admin/settings/grimmory-admin', {
					body: { username: adminUsername, password: adminPassword }
				})
			);
			adminPassword = '';
		} finally {
			savingAdmin = false;
		}
	}

	let hardcoverKey = $state('');
	let savingHardcover = $state(false);

	async function saveHardcover(event: SubmitEvent) {
		event.preventDefault();
		savingHardcover = true;
		try {
			settings = unwrap(await api.POST('/api/admin/settings/hardcover', { body: { hardcover_api_key: hardcoverKey } }));
			hardcoverKey = '';
		} finally {
			savingHardcover = false;
		}
	}
</script>

<svelte:head>
	<title>Library settings — Book Knook</title>
</svelte:head>

<div class="list-header">
	<span class="list-title">Library settings</span>
</div>

<div class="settings-section">
	<div class="settings-section-title">Library cross-check</div>
	<p class="empty-state">Flags which TBR books are already in the library. Leave blank to disable.</p>

	<form class="settings-form" onsubmit={saveLibrary}>
		<label>
			Grimmory base URL
			<input type="url" placeholder="https://grimmory.example.com" bind:value={libraryBaseUrl} />
		</label>
		<label>
			Username
			<input type="text" bind:value={libraryUsername} />
		</label>
		<label>
			Password
			<input
				type="password"
				placeholder={settings.library_settings?.password_set ? 'Leave blank to keep current password' : ''}
				bind:value={libraryPassword}
			/>
		</label>
		<label>
			Sync interval (minutes)
			<input type="number" min="1" bind:value={syncIntervalMinutes} />
		</label>
		<button type="submit" class="btn btn-accent" disabled={savingLibrary}>Save</button>
	</form>
</div>

<div class="settings-section">
	<div class="settings-section-title">Grimmory admin credentials</div>
	<p class="empty-state"></p>

	<form class="settings-form" onsubmit={saveAdmin}>
		<label>
			Username
			<input type="text" bind:value={adminUsername} />
		</label>
		<label>
			Password
			<input
				type="password"
				placeholder={settings.grimmory_admin_settings?.password_set ? 'Leave blank to keep current password' : ''}
				bind:value={adminPassword}
			/>
		</label>
		<button type="submit" class="btn btn-accent" disabled={savingAdmin}>Save</button>
	</form>
</div>

<div class="settings-section">
	<div class="settings-section-title">Book search</div>
	<p class="empty-state"></p>

	<form class="settings-form" onsubmit={saveHardcover}>
		<label>
			Hardcover API key
			<input
				type="password"
				placeholder={settings.hardcover_api_key_set ? 'Leave blank to keep current key' : ''}
				bind:value={hardcoverKey}
			/>
		</label>
		<button type="submit" class="btn btn-accent" disabled={savingHardcover}>Save</button>
	</form>
</div>
