<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { api, unwrap } from '$lib/api/client';
	import type { components } from '$lib/api/schema';
	import type { TBREntry } from '$lib/utils/entries';

	type PhysicalSession = components['schemas']['PhysicalReadingSessionOut'];

	// Not edit-mode gated - logging sessions is a normal-mode feature. Just the add/edit form now;
	// the session list (merged with ebook/audiobook sessions) lives in SessionLogModal, which
	// triggers openForEdit() below to reuse this same form/popover for a physical row's edit.
	let { entry }: { entry: TBREntry } = $props();

	// datetime-local inputs are in the browser's own timezone; convert to/from the UTC instant
	// the backend stores (app/dates.py:parse_instant).
	function toDatetimeLocal(iso: string): string {
		const d = new Date(iso);
		const pad = (n: number) => String(n).padStart(2, '0');
		return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
	}
	function fromDatetimeLocal(local: string): string {
		return new Date(local).toISOString();
	}

	type FormState = { startTime: string; endTime: string; startPage: string; endPage: string };
	const emptyForm: FormState = { startTime: '', endTime: '', startPage: '', endPage: '' };

	let editingId = $state<number | null>(null);
	let form = $state<FormState>({ ...emptyForm });
	let saving = $state(false);
	let error = $state('');
	let popoverEl = $state<HTMLDivElement>();

	// Called by SessionLogModal's edit button for a physical row - opens this same form
	// pre-filled, rather than duplicating an edit UI in the log itself.
	export function openForEdit(session: PhysicalSession) {
		startEditing(session);
		popoverEl?.showPopover();
	}

	// Fires on every open/close, however triggered. No session selected on open means "+".
	function onToggle(event: ToggleEvent) {
		if (event.newState === 'open') {
			if (editingId === null) {
				error = '';
				form = { ...emptyForm };
			}
		} else {
			editingId = null;
			error = '';
		}
	}

	function startEditing(session: PhysicalSession) {
		form = {
			startTime: toDatetimeLocal(session.start_time),
			endTime: toDatetimeLocal(session.end_time),
			startPage: String(session.start_page),
			endPage: String(session.end_page)
		};
		editingId = session.id;
	}

	async function save(event: SubmitEvent) {
		event.preventDefault();
		error = '';
		if (!form.startTime || !form.endTime) {
			error = 'Start and end time are required.';
			return;
		}
		const startPage = Number(form.startPage);
		const endPage = Number(form.endPage);
		if (!Number.isFinite(startPage) || !Number.isFinite(endPage) || startPage < 0 || endPage < startPage) {
			error = 'End page must be at or after the start page.';
			return;
		}
		const body = {
			start_time: fromDatetimeLocal(form.startTime),
			end_time: fromDatetimeLocal(form.endTime),
			start_page: startPage,
			end_page: endPage
		};
		if (new Date(body.end_time) <= new Date(body.start_time)) {
			error = 'End time must be after start time.';
			return;
		}
		saving = true;
		try {
			if (editingId !== null) {
				unwrap(
					await api.POST('/api/tbr/{entry_id}/physical-sessions/{session_id}', {
						params: { path: { entry_id: entry.id, session_id: editingId } },
						body
					})
				);
			} else {
				unwrap(
					await api.POST('/api/tbr/{entry_id}/physical-sessions', {
						params: { path: { entry_id: entry.id } },
						body
					})
				);
			}
			popoverEl?.hidePopover();
			await invalidateAll();
		} catch (err) {
			error = err instanceof Error ? err.message : 'Could not save this session.';
		} finally {
			saving = false;
		}
	}
</script>

<div
	bind:this={popoverEl}
	id="physical-session-form-{entry.id}"
	popover
	class="sheet"
	ontoggle={onToggle}
>
	<div class="sheet-handle"></div>
	<div class="sheet-title">Physical reading sessions</div>

	<form class="settings-form" onsubmit={save}>
		{#if error}<p class="error">{error}</p>{/if}
		<label>
			Started
			<input type="datetime-local" bind:value={form.startTime} required />
		</label>
		<label>
			Finished
			<input type="datetime-local" bind:value={form.endTime} required />
		</label>
		<label>
			Start page
			<input type="number" min="0" step="1" bind:value={form.startPage} required />
		</label>
		<label>
			End page
			<input type="number" min="0" step="1" bind:value={form.endPage} required />
		</label>
		<div class="dates-edit-actions">
			<button type="submit" class="btn btn-accent" disabled={saving}>
				{editingId !== null ? 'Update' : 'Save'}
			</button>
			<button
				type="button"
				class="btn btn-ghost"
				popovertarget="physical-session-form-{entry.id}"
				popovertargetaction="hide"
			>
				Cancel
			</button>
		</div>
	</form>
</div>
