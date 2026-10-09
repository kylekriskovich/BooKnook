<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { api, unwrap } from '$lib/api/client';
	import type { TBREntry } from '$lib/utils/entries';

	let { entry }: { entry: TBREntry } = $props();

	// svelte-ignore state_referenced_locally
	let ownsPhysical = $state(entry.owns_physical);
	let saving = $state(false);

	// This edition's own page count (Decision 9) - separate from books.page_count since a
	// physical printing can differ from Grimmory's cataloged digital file.
	// bind:value coerces this to number|undefined regardless of initial type - keep it typed
	// that way or savePageCount's read of it throws at runtime.
	// svelte-ignore state_referenced_locally
	let pageCount = $state<number | undefined>(entry.physical_page_count ?? undefined);
	let savingPageCount = $state(false);

	async function toggle() {
		const next = !ownsPhysical;
		ownsPhysical = next;
		saving = true;
		try {
			unwrap(
				await api.POST('/api/tbr/{entry_id}/physical', {
					params: { path: { entry_id: entry.id } },
					body: { owns_physical: next }
				})
			);
			await invalidateAll();
		} catch (error) {
			ownsPhysical = !next;
			throw error;
		} finally {
			saving = false;
		}
	}

	async function savePageCount() {
		const parsed = pageCount ?? null;
		if (parsed !== null && (!Number.isFinite(parsed) || parsed <= 0)) return;
		savingPageCount = true;
		try {
			unwrap(
				await api.POST('/api/tbr/{entry_id}/physical-page-count', {
					params: { path: { entry_id: entry.id } },
					body: { physical_page_count: parsed }
				})
			);
			await invalidateAll();
		} finally {
			savingPageCount = false;
		}
	}
</script>

<div class="settings-section">
	<label class="switch-row">
		<span class="switch-row-label">Own Physical</span>
		<span class="switch">
			<input type="checkbox" checked={ownsPhysical} disabled={saving} onchange={toggle} />
			<span class="switch-track"><span class="switch-thumb"></span></span>
		</span>
	</label>
	{#if ownsPhysical}
		<!-- Enter submits the form rather than blurring the input, so save on both: onblur covers
		     tabbing/clicking away, the submit handler covers pressing Enter. -->
		<form
			class="settings-form"
			onsubmit={(event) => {
				event.preventDefault();
				savePageCount();
			}}
		>
			<label>
				This edition's page count
				<input
					type="number"
					min="1"
					step="1"
					placeholder="e.g. 450"
					bind:value={pageCount}
					disabled={savingPageCount}
					onblur={savePageCount}
				/>
			</label>
		</form>
	{/if}
</div>
