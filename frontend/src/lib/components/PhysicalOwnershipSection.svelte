<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { api, unwrap } from '$lib/api/client';
	import type { TBREntry } from '$lib/utils/entries';

	let { entry }: { entry: TBREntry } = $props();

	// svelte-ignore state_referenced_locally
	let ownsPhysical = $state(entry.owns_physical);
	let saving = $state(false);

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
</script>

<div class="settings-section">
	<label class="switch-row">
		<span class="switch-row-label">Own Physical</span>
		<span class="switch">
			<input type="checkbox" checked={ownsPhysical} disabled={saving} onchange={toggle} />
			<span class="switch-track"><span class="switch-thumb"></span></span>
		</span>
	</label>
</div>
