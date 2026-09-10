<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { api, unwrap } from '$lib/api/client';
	import type { TBREntry } from '$lib/utils/entries';

	// Only ever mounted while editingDates is true (see the {#if} wrapper in book/[id]/+page.svelte)
	// - read-only dates show as metadata rows in BookHeader instead now, so there's nothing for this
	// component to render outside edit mode at all, just the form. Still needs editingDates back as
	// a bindable prop so Cancel/a successful save can close it.
	let { entry, editingDates = $bindable(false) }: { entry: TBREntry; editingDates?: boolean } =
		$props();

	// The parent wraps this component in {#key entry.id} (see book/[id]/+page.svelte), so a new
	// instance — and a fresh read of `entry` — is created on every book navigation; capturing only
	// the initial value here is intentional, not the stale-on-navigation bug this warning usually
	// flags.
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
