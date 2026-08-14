<script lang="ts">
	import { onMount } from 'svelte';
	import { api, ApiError, unwrap } from '$lib/api/client';

	let {
		wantToReadShelfId: initialWantToReadShelfId,
		syncToDeviceEnabled: initialSyncToDeviceEnabled,
		syncToDeviceShelfId: initialSyncToDeviceShelfId
	}: {
		wantToReadShelfId: number | null;
		syncToDeviceEnabled: boolean;
		syncToDeviceShelfId: number | null;
	} = $props();

	let shelves = $state<{ id: number; name: string }[]>([]);
	let loadingShelves = $state(true);
	let shelvesError = $state<string | null>(null);

	// This component is remounted whenever its parent's `data.settings` is (the settings page has
	// no route param — see +page.svelte's own svelte-ignore for the same reasoning), so capturing
	// only the initial value here is fine.
	// svelte-ignore state_referenced_locally
	let wantToReadShelfId = $state(initialWantToReadShelfId);
	// svelte-ignore state_referenced_locally
	let syncToDeviceEnabled = $state(initialSyncToDeviceEnabled);
	// svelte-ignore state_referenced_locally
	let syncToDeviceShelfId = $state(initialSyncToDeviceShelfId);

	let saving = $state(false);
	let saved = $state(false);
	let saveError = $state<string | null>(null);

	let reconnectPassword = $state('');
	let reconnecting = $state(false);

	function describeError(err: unknown): string {
		return err instanceof ApiError ? err.message : 'Could not reach the server — try again.';
	}

	async function loadShelves() {
		loadingShelves = true;
		try {
			const result = unwrap(await api.GET('/api/settings/shelves'));
			shelves = result.shelves;
			shelvesError = result.error ?? null;
		} catch (err) {
			shelvesError = describeError(err);
		} finally {
			loadingShelves = false;
		}
	}

	onMount(loadShelves);

	async function reconnectAndRetry(event: SubmitEvent) {
		event.preventDefault();
		reconnecting = true;
		try {
			const result = unwrap(await api.POST('/api/settings/sync', { body: { password: reconnectPassword } }));
			reconnectPassword = '';
			if (result.error) {
				shelvesError = result.error;
			} else {
				await loadShelves();
			}
		} catch (err) {
			shelvesError = describeError(err);
		} finally {
			reconnecting = false;
		}
	}

	async function save(event: SubmitEvent) {
		event.preventDefault();
		saving = true;
		saved = false;
		saveError = null;
		try {
			unwrap(
				await api.POST('/api/settings/shelves', {
					body: {
						want_to_read_shelf_id: wantToReadShelfId,
						sync_to_device_enabled: syncToDeviceEnabled,
						sync_to_device_shelf_id: syncToDeviceShelfId
					}
				})
			);
			saved = true;
		} catch (err) {
			saveError = describeError(err);
		} finally {
			saving = false;
		}
	}
</script>

<div class="settings-section" id="shelf-sync-section">
	<div class="settings-section-title">Shelf sync</div>
	{#if loadingShelves}
		<p class="empty-state">Loading your Grimmory shelves…</p>
	{:else if shelvesError === 'reconnect_needed'}
		<p class="error">Your Grimmory session has expired — enter your password to reconnect.</p>
		<form class="settings-form" onsubmit={reconnectAndRetry}>
			<label>
				Grimmory password
				<input type="password" autocomplete="current-password" required bind:value={reconnectPassword} />
			</label>
			<button type="submit" class="btn btn-accent" disabled={reconnecting}>Reconnect</button>
		</form>
	{:else if shelvesError}
		<p class="error">{shelvesError}</p>
	{:else}
		<p class="empty-state">
			Keeps a Grimmory shelf in sync with your Want to Read list, and optionally a second shelf
			for the KOReader plugin.
		</p>
		{#if saveError}<p class="error">{saveError}</p>{/if}
		<form class="settings-form" onsubmit={save}>
			<label>
				Want to Read shelf
				<select bind:value={wantToReadShelfId}>
					<option value={null}>Auto-create "Want to Read" on next sync</option>
					{#each shelves as shelf (shelf.id)}
						<option value={shelf.id}>{shelf.name}</option>
					{/each}
				</select>
			</label>
			<label class="settings-checkbox-row">
				<input type="checkbox" bind:checked={syncToDeviceEnabled} />
				Sync to Device
			</label>
			<p class="empty-state">
				Please note this will require you to set up the grimmory.koplugin separately.
			</p>
			{#if syncToDeviceEnabled}
				<label>
					Sync to Device shelf
					<select bind:value={syncToDeviceShelfId}>
						<option value={null}>Auto-create "Booknook: Sync to Device" on next sync</option>
						{#each shelves as shelf (shelf.id)}
							<option value={shelf.id}>{shelf.name}</option>
						{/each}
					</select>
				</label>
			{/if}
			<button type="submit" class="btn btn-accent" disabled={saving}>Save</button>
			{#if saved}<p class="sync-success">Saved.</p>{/if}
		</form>
	{/if}
</div>
