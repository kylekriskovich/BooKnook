<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { api, unwrap } from '$lib/api/client';
	import type { components } from '$lib/api/schema';
	import { auth } from '$lib/stores/auth.svelte';
	import type { TBREntry } from '$lib/utils/entries';
	import { formatDuration, formatWhen } from '$lib/utils/dates';

	type SessionLogEntry = components['schemas']['SessionLogEntryOut'];
	type PhysicalSession = components['schemas']['PhysicalReadingSessionOut'];

	// Merged, newest-first (server-sorted) log across every source a book can have sessions from.
	// Ebook/audiobook rows are delete-only (Grimmory has no session edit/delete API - see
	// app/models.py:cached_reading_sessions); a physical row's edit reuses
	// PhysicalReadingSessionsSection's own form via onEditPhysical rather than duplicating one here.
	let {
		entry,
		sessions,
		onEditPhysical
	}: {
		entry: TBREntry;
		sessions: SessionLogEntry[];
		onEditPhysical: (session: PhysicalSession) => void;
	} = $props();

	let popoverEl = $state<HTMLDivElement>();
	let removingKey = $state<string | null>(null);
	let grouping = $state(false);
	let confirmingGroup = $state(false);

	// Grimmory session ids and physical session ids are unrelated integer spaces - source
	// disambiguates them, so a row's key (and the delete endpoint's path) needs both.
	function rowKey(row: SessionLogEntry): string {
		return `${row.source}:${row.id}`;
	}

	// A confirmed Grimmory bug fragments one real listening/reading stretch into many rows that
	// all share the stretch's original start_time (see app/models.py:group_duplicate_reading_sessions).
	// Detected here only to decide whether to offer the grouping action - the actual merge always
	// happens server-side against the full, current data.
	let hasBursts = $derived(
		(() => {
			const counts = new Map<string, number>();
			for (const row of sessions) {
				if (row.source === 'physical') continue;
				const key = `${row.source}:${row.start_time}`;
				counts.set(key, (counts.get(key) ?? 0) + 1);
			}
			return [...counts.values()].some((count) => count > 1);
		})()
	);
	// Only offered once a book is finished (a still-"reading" one can have a burst split across
	// multiple live fetches, so merging mid-stream risks collapsing an incomplete stretch) and only
	// to the admin - same UI-shortcut convention as everywhere else is_admin is checked (see
	// app/main.py:_is_admin's docstring); this is a visibility hint, not real access control, since
	// the endpoint itself has no admin check either.
	let canGroup = $derived(entry.status === 'finished' && hasBursts && auth.user?.is_admin);

	async function confirmGroup() {
		grouping = true;
		try {
			unwrap(
				await api.POST('/api/tbr/{entry_id}/sessions/group', {
					params: { path: { entry_id: entry.id } }
				})
			);
			await invalidateAll();
		} finally {
			grouping = false;
			confirmingGroup = false;
		}
	}

	function sourceLabel(source: string): string {
		if (source === 'ebook') return 'Reading';
		if (source === 'audiobook') return 'Listening';
		return 'Physical';
	}

	function badgeClass(source: string): string {
		if (source === 'ebook') return 'badge-ebook';
		if (source === 'audiobook') return 'badge-audiobook';
		return 'badge-physical';
	}

	// Duration alone hides whether a short session actually moved any pages - always pair it with
	// pages/progress rather than picking one or the other, so a "1m" session reads as either
	// "1m · 2 pages" or "1m · no pages read" instead of just "1m".
	function subLine(row: SessionLogEntry): string {
		const duration =
			row.end_time && row.duration_seconds ? formatDuration(row.start_time, row.end_time) : null;
		if (row.source === 'physical') {
			const pages = `p.${row.start_page}–${row.end_page}`;
			return duration ? `${pages} · ${duration}` : pages;
		}
		if (row.source === 'ebook') {
			const pages = row.pages ? `${row.pages} pages` : 'no pages read';
			return duration ? `${duration} · ${pages}` : pages;
		}
		// audiobook: no page concept - Grimmory never tracks audio position in pages.
		if (duration) return duration;
		if (row.progress_delta) return `+${Math.round(row.progress_delta)}%`;
		if (row.end_progress != null) return `${Math.round(row.end_progress)}%`;
		return sourceLabel(row.source);
	}

	function editPhysical(row: SessionLogEntry) {
		popoverEl?.hidePopover();
		onEditPhysical({
			id: row.id,
			start_time: row.start_time,
			end_time: row.end_time ?? row.start_time,
			start_page: row.start_page ?? 0,
			end_page: row.end_page ?? 0
		});
	}

	async function remove(row: SessionLogEntry) {
		removingKey = rowKey(row);
		try {
			unwrap(
				await api.POST('/api/tbr/{entry_id}/sessions/{source}/{session_id}/remove', {
					params: { path: { entry_id: entry.id, source: row.source, session_id: row.id } }
				})
			);
			await invalidateAll();
		} finally {
			removingKey = null;
		}
	}
</script>

<div bind:this={popoverEl} id="session-log-{entry.id}" popover class="sheet">
	<div class="sheet-handle"></div>
	<div class="sheet-title">Session log</div>

	{#if canGroup}
		<div class="settings-form">
			{#if confirmingGroup}
				<p class="error">
					This permanently merges duplicate-looking sessions into one per burst, summing their
					time (no listening/reading time is lost). You'll no longer be able to delete an
					individual session within a merged burst on its own. This can't be undone.
				</p>
				<div class="dates-edit-actions">
					<button type="button" class="btn btn-accent" disabled={grouping} onclick={confirmGroup}>
						{grouping ? 'Merging…' : 'Confirm merge'}
					</button>
					<button type="button" class="btn btn-ghost" onclick={() => (confirmingGroup = false)}>
						Cancel
					</button>
				</div>
			{:else}
				<button type="button" class="btn btn-ghost" onclick={() => (confirmingGroup = true)}>
					Group duplicate sessions
				</button>
			{/if}
		</div>
	{/if}

	{#if sessions.length}
		<div class="settings-list">
			{#each sessions as row (rowKey(row))}
				<div class="settings-row physical-session-row">
					<div>
						<div class="session-log-row-header">
							<span class="badge {badgeClass(row.source)}">{sourceLabel(row.source)}</span>
							<span class="settings-row-label">{formatWhen(row.start_time)}</span>
						</div>
						<span class="physical-session-sub">{subLine(row)}</span>
					</div>
					<div class="physical-session-actions">
						{#if row.source === 'physical'}
							<button
								type="button"
								class="iconbtn"
								aria-label="Edit session"
								onclick={() => editPhysical(row)}
							>
								<svg viewBox="0 -960 960 960" fill="currentColor" aria-hidden="true">
									<path
										d="M200-200h57l391-391-57-57-391 391v57Zm-80 80v-170l528-527q11-12 26-18t31-6q16 0 30.5 6t25.5 18l55 56q12 11 18 25.5t6 30.5q0 16-6 31t-18 26L293-120H120Zm640-584-56-56 56 56Z"
									/>
								</svg>
							</button>
						{/if}
						<button
							type="button"
							class="iconbtn"
							aria-label="Delete session"
							disabled={removingKey === rowKey(row)}
							onclick={() => remove(row)}
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
		<p class="empty-state">No reading sessions logged yet.</p>
	{/if}
</div>

<style>
	.session-log-row-header {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}
</style>
