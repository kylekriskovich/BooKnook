<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { api, unwrap } from '$lib/api/client';
	import type { TBREntry } from '$lib/utils/entries';

	let { entry }: { entry: TBREntry } = $props();

	// The parent wraps this component in {#key entry.id} (see book/[id]/+page.svelte), so a new
	// instance — and a fresh read of `entry` — is created on every book navigation; capturing only
	// the initial value here is intentional, not the stale-on-navigation bug this warning usually
	// flags.
	let editingDates = $state(false);
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
	<input type="checkbox" id="dates-edit-toggle" class="edit-toggle" bind:checked={editingDates} />
	<div class="settings-section-header">
		<div class="settings-section-title">Reading dates</div>
		<label for="dates-edit-toggle" class="iconbtn" aria-label="Edit reading dates">
			<svg viewBox="0 -960 960 960" fill="currentColor" aria-hidden="true">
				<path
					d="M200-200h57l391-391-57-57-391 391v57Zm-80 80v-170l528-527q11-12 26-18t31-6q16 0 30.5 6t25.5 18l55 56q12 11 18 25.5t6 30.5q0 16-6 31t-18 26L293-120H120Zm640-584-56-56 56 56Z"
				/>
			</svg>
		</label>
	</div>

	<div class="settings-list dates-readonly">
		<div class="settings-row">
			<span class="settings-row-label">Started reading</span>
			<span class="settings-row-value">{entry.started_at ?? 'Not set'}</span>
		</div>
		{#if entry.status === 'finished'}
			<div class="settings-row">
				<span class="settings-row-label">Finished reading</span>
				<span class="settings-row-value">{entry.finished_at ? entry.finished_at.slice(0, 10) : 'Not set'}</span>
			</div>
		{/if}
	</div>

	<form class="settings-form dates-edit-form" onsubmit={saveDates}>
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
			<label for="dates-edit-toggle" class="btn btn-ghost">Cancel</label>
		</div>
	</form>
</div>
