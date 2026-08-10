<script lang="ts">
	import type { components } from '$lib/api/schema';
	import { paletteFor } from '$lib/utils/palette';

	type DayCell = components['schemas']['DayCellOut'];
	type BookSpan = components['schemas']['BookSpanOut'];

	let {
		cell,
		prevCell,
		nextCell,
		spansById
	}: {
		cell: DayCell;
		prevCell: DayCell | null;
		nextCell: DayCell | null;
		spansById: Map<number, BookSpan>;
	} = $props();

	const day = $derived(Number(cell.date.slice(-2)));
	const coverSpans = $derived(cell.cover_entry_ids.map((id) => spansById.get(id)!).slice(0, 3));
	const overflow = $derived(Math.max(0, cell.cover_entry_ids.length - 3));

	function direction(span: BookSpan, index: number): 'center' | 'left' | 'right' {
		if (index === 0 || span.start === span.end) return 'center';
		return span.start === cell.date ? 'right' : 'left';
	}

	// A bar bridges into the neighboring cell whenever that same book's bar is also rendered
	// there — mirrors app/templates/_calendar_section.html's `span in prev_cell.bar_spans` check
	// (membership anywhere in the neighbor's bars, not just the same lane index) — and never
	// across a row wrap or a greyed-out (out-of-month) day on either side. See DayCellOut's
	// docstring in app/schemas.py for why bar_entry_ids can have interior null gaps.
	function connectsLeft(entryId: number): boolean {
		return cell.in_month && !!prevCell?.in_month && prevCell.bar_entry_ids.includes(entryId);
	}
	function connectsRight(entryId: number): boolean {
		return cell.in_month && !!nextCell?.in_month && nextCell.bar_entry_ids.includes(entryId);
	}
</script>

<div class="calendar-day{cell.in_month ? '' : ' is-out-of-month'}{cell.is_today ? ' is-today' : ''}">
	<span class="calendar-day-num">{day}</span>

	{#if !cell.is_future && coverSpans.length}
		<div class="calendar-day-cover-stack">
			{#each coverSpans as span, index (span.entry_id)}
				<div class="calendar-day-cover-card calendar-day-cover-card--{direction(span, index)}">
					{#if span.book.cover_url}
						<img src={span.book.cover_url} alt="" class="calendar-day-cover-img" />
					{:else}
						<div class="calendar-day-cover-placeholder" style="background:{paletteFor(span.book.id)}"></div>
					{/if}
					{#if span.status === 'finished' && cell.date === span.end}
						<span class="calendar-day-badge calendar-day-badge-finished" aria-label="Finished">
							<svg viewBox="0 -960 960 960" fill="currentColor" aria-hidden="true">
								<path d="M382-240 154-468l57-57 171 171 356-356 57 57-413 413Z" />
							</svg>
						</span>
					{:else if span.status === 'reading'}
						<span class="calendar-day-badge calendar-day-badge-reading" aria-label="In progress">
							<svg viewBox="0 -960 960 960" fill="currentColor" aria-hidden="true">
								<path d="M320-200v-560l440 280-440 280Z" />
							</svg>
						</span>
					{/if}
				</div>
			{/each}
			{#if overflow > 0}
				<span class="calendar-day-overflow-chip">+{overflow}</span>
			{/if}
		</div>
	{/if}

	{#if cell.bar_entry_ids.length}
		<div class="calendar-day-bars">
			{#each cell.bar_entry_ids as entryId, laneIndex (entryId ?? `empty-${laneIndex}`)}
				{#if entryId === null}
					<span class="calendar-day-bar calendar-day-bar--empty"></span>
				{:else}
					{@const span = spansById.get(entryId)!}
					<span
						class="calendar-day-bar{connectsLeft(entryId) ? ' calendar-day-bar--connects-left' : ''}{connectsRight(entryId) ? ' calendar-day-bar--connects-right' : ''}"
						style="background:{span.book.cover_color ?? paletteFor(span.book.id)}"
					></span>
				{/if}
			{/each}
		</div>
	{/if}
</div>
