<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { api, unwrap } from '$lib/api/client';
	import type { TBREntry } from '$lib/utils/entries';

	// Only mounted while editingDates is true; read-only dates render in BookHeader instead.
	let { entry, editingDates = $bindable(false) }: { entry: TBREntry; editingDates?: boolean } =
		$props();

	// {#key entry.id} in the parent remounts this on every book navigation, so capturing only
	// the initial value here isn't the stale-on-navigation bug this warning usually flags.
	// svelte-ignore state_referenced_locally
	let startedAt = $state(entry.started_at ?? '');
	// svelte-ignore state_referenced_locally
	let finishedAt = $state(entry.finished_at?.slice(0, 10) ?? '');
	let saving = $state(false);

	async function saveDates(event: SubmitEvent) {
		event.preventDefault();
		saving = true;
		try {
			unwrap(
				await api.POST('/api/tbr/{entry_id}/dates', {
					params: { path: { entry_id: entry.id } },
					body: { started_at: startedAt, finished_at: finishedAt }
				})
			);
			editingDates = false;
			await invalidateAll();
		} finally {
			saving = false;
		}
	}
</script>

<div class="settings-section">
	<div class="settings-section-header">
		<div class="settings-section-title">Reading dates</div>
	</div>

	<form class="settings-form" onsubmit={saveDates}>
		<label>
			Started reading
			<input type="date" bind:value={startedAt} />
		</label>
		{#if entry.status === 'finished'}
			<label>
				Finished reading
				<input type="date" bind:value={finishedAt} />
			</label>
		{/if}
		<div class="dates-edit-actions">
			<button type="submit" class="btn btn-accent" disabled={saving}>Save</button>
			<button type="button" class="btn btn-ghost" onclick={() => (editingDates = false)}>Cancel</button>
		</div>
	</form>
</div>
