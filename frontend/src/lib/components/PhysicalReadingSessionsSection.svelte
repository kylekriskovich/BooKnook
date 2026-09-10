<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { api, unwrap } from '$lib/api/client';
	import type { components } from '$lib/api/schema';
	import type { TBREntry } from '$lib/utils/entries';

	type PhysicalSession = components['schemas']['PhysicalReadingSessionOut'];

	// Not edit-mode gated - logging/reviewing sessions is a normal-mode feature. The list and
	// add/edit form both live inside one popup, opened via the header's "+" or a row's edit button.
	let { entry, sessions }: { entry: TBREntry; sessions: PhysicalSession[] } = $props();

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

	function formatDuration(startIso: string, endIso: string): string {
		const minutes = Math.round((Date.parse(endIso) - Date.parse(startIso)) / 60_000);
		const hours = Math.floor(minutes / 60);
		const mins = minutes % 60;
		if (hours && mins) return `${hours}h ${mins}m`;
		if (hours) return `${hours}h`;
		return `${mins}m`;
	}

	function formatWhen(iso: string): string {
		return new Date(iso).toLocaleString(undefined, {
			month: 'short',
			day: 'numeric',
			hour: 'numeric',
			minute: '2-digit'
		});
	}

	type FormState = { startTime: string; endTime: string; startPage: string; endPage: string };
	const emptyForm: FormState = { startTime: '', endTime: '', startPage: '', endPage: '' };

	let editingId = $state<number | null>(null);
	let form = $state<FormState>({ ...emptyForm });
	let saving = $state(false);
	let error = $state('');
	let removingId = $state<number | null>(null);
	let popoverEl = $state<HTMLDivElement>();

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
		popoverEl?.showPopover();
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

	async function remove(session: PhysicalSession) {
		removingId = session.id;
		try {
			unwrap(
				await api.POST('/api/tbr/{entry_id}/physical-sessions/{session_id}/remove', {
					params: { path: { entry_id: entry.id, session_id: session.id } }
				})
			);
			await invalidateAll();
		} finally {
			removingId = null;
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

	<div class="settings-section-title">Logged sessions</div>
	{#if sessions.length}
		<div class="settings-list">
			{#each sessions as session (session.id)}
				<div class="settings-row physical-session-row">
					<div>
						<span class="settings-row-label">{formatWhen(session.start_time)}</span>
						<span class="physical-session-sub">
							p.{session.start_page}–{session.end_page} · {formatDuration(session.start_time, session.end_time)}
						</span>
					</div>
					<div class="physical-session-actions">
						<button type="button" class="iconbtn" aria-label="Edit session" onclick={() => startEditing(session)}>
							<svg viewBox="0 -960 960 960" fill="currentColor" aria-hidden="true">
								<path
									d="M200-200h57l391-391-57-57-391 391v57Zm-80 80v-170l528-527q11-12 26-18t31-6q16 0 30.5 6t25.5 18l55 56q12 11 18 25.5t6 30.5q0 16-6 31t-18 26L293-120H120Zm640-584-56-56 56 56Z"
								/>
							</svg>
						</button>
						<button
							type="button"
							class="iconbtn"
							aria-label="Delete session"
							disabled={removingId === session.id}
							onclick={() => remove(session)}
						>
							<svg viewBox="0 -960 960 960" fill="currentColor" aria-hidden="true">
								<path
									d="M280-120q-33 0-56.5-23.5T200-200v-520h-40v-80h200v-40h240v40h200v80h-40v520q0 33-23.5 56.5T680-120H280Zm400-600H280v520h400v-520ZM360-280h80v-360h-80v360Zm160 0h80v-360h-80v360ZM280-720v520-520Z"
								/>
							</svg>
						</button>
					</div>
				</div>
			{/each}
		</div>
	{:else}
		<p class="empty-state">No physical sessions logged yet.</p>
	{/if}
</div>
