<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { api } from '$lib/api/client';
	import AdminEntry from '$lib/components/AdminEntry.svelte';
	import AdminMatchSheet from '$lib/components/AdminMatchSheet.svelte';
	import AdminPairSheet from '$lib/components/AdminPairSheet.svelte';

	let { data } = $props();

	let syncing = $state(false);

	async function syncNow(event: SubmitEvent) {
		event.preventDefault();
		syncing = true;
		try {
			await api.POST('/api/admin/library-sync');
			await invalidateAll();
		} finally {
			syncing = false;
		}
	}
</script>

<svelte:head>
	<title>Admin — Book Knook</title>
</svelte:head>

<div class="admin-header">
	<h1>All TBR requests</h1>
	<span class="admin-header-links">
		<a href="/admin/settings" class="settings-link">Library settings</a>
	</span>
</div>

{#if data.admin.library_check_enabled}
	<div class="sync-bar">
		<span class="sync-status">
			{#if data.admin.last_synced_at}
				Library last synced: {data.admin.last_synced_at}
			{:else}
				Library never synced yet
			{/if}
			{#if data.admin.last_error}
				<span class="sync-error">(last attempt failed: {data.admin.last_error})</span>
			{/if}
		</span>
		<form onsubmit={syncNow}>
			<button type="submit" class="btn btn-ghost sync-now-btn" disabled={syncing}>Sync now</button>
		</form>
	</div>
{/if}

{#if !data.admin.needed_entries.length && !data.admin.owned_entries.length && !data.admin.audiobook_entries.length}
	<p class="empty-state">Nothing here yet.</p>
{:else if data.admin.library_check_enabled}
	<input type="radio" name="admin-view" id="admin-view-needed" class="view-radio" checked />
	<input type="radio" name="admin-view" id="admin-view-owned" class="view-radio" />
	<input type="radio" name="admin-view" id="admin-view-audiobooks" class="view-radio" />

	<div class="view-tabs admin-view-tabs">
		<label for="admin-view-needed" class="view-tab">NEED TO ACQUIRE</label>
		<label for="admin-view-owned" class="view-tab">IN LIBRARY</label>
		<label for="admin-view-audiobooks" class="view-tab">AUDIOBOOKS</label>
	</div>

	<div class="admin-view-needed-section">
		{#if data.admin.needed_entries.length}
			<ul class="admin-entries">
				{#each data.admin.needed_entries as row (row.id)}
					<AdminEntry {row} mode="needed" />
				{/each}
			</ul>
		{:else}
			<p class="empty-state">Nothing to acquire right now.</p>
		{/if}
	</div>

	<div class="admin-view-owned-section">
		{#if data.admin.owned_entries.length}
			<ul class="admin-entries">
				{#each data.admin.owned_entries as row (row.grimmory_id ?? row.id)}
					<AdminEntry {row} mode="owned" />
				{/each}
			</ul>
		{:else}
			<p class="empty-state">Library catalog is empty — try syncing.</p>
		{/if}
	</div>

	<div class="admin-view-audiobooks-section">
		{#if data.admin.audiobook_entries.length}
			<ul class="admin-entries">
				{#each data.admin.audiobook_entries as row (row.grimmory_id ?? row.id)}
					<AdminEntry {row} mode="audiobook" />
				{/each}
			</ul>
		{:else}
			<p class="empty-state">No audiobooks in the library.</p>
		{/if}
	</div>
	<AdminMatchSheet />
	<AdminPairSheet />
{:else}
	<ul class="admin-entries">
		{#each data.admin.needed_entries as row (row.id)}
			<AdminEntry {row} mode="needed" matchable={false} />
		{/each}
	</ul>
{/if}
