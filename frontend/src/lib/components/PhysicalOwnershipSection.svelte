<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { api, unwrap } from '$lib/api/client';
	import type { TBREntry } from '$lib/utils/entries';

	let { entry }: { entry: TBREntry } = $props();

	// svelte-ignore state_referenced_locally
	let ownsPhysical = $state(entry.owns_physical);
	let saving = $state(false);

	// This edition's own page count (DESIGN-multi-edition-refactor.md Decision 9) - deliberately
	// separate from books.page_count, since a physical printing can have a genuinely different
	// total than whichever digital file Grimmory has cataloged. Only shown/settable while
	// ownsPhysical is true - it exists purely to convert a physical session's pages into a
	// percentage (see app/stat_tiles.py:physical_session_to_grimmory_shape), so there's nothing
	// for it to do otherwise.
	// A number input's bind:value is coerced to a number (or undefined when empty) by Svelte
	// itself, regardless of how this is typed/initialized - keep the state's actual type in sync
	// with that instead of a string, or savePageCount's read of it throws at runtime.
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
