<script lang="ts">
	import { api } from '$lib/api/client';
	import { auth } from '$lib/stores/auth.svelte';
	import type { components } from '$lib/api/schema';
	import { paletteFor } from '$lib/utils/palette';
	import CalendarDayCell from './CalendarDayCell.svelte';

	type Calendar = components['schemas']['CalendarOut'];

	let { calendar }: { calendar: Calendar } = $props();

	const spansById = $derived(new Map(calendar.spans.map((span) => [span.entry_id, span])));

	async function setCalendarView(view: 'grid' | 'list') {
		if (!auth.user || auth.user.calendar_view_preference === view) return;
		auth.user.calendar_view_preference = view;
		await api.POST('/api/preferences/calendar-view', { body: { view } });
	}

	function formatSpanRange(span: Calendar['spans'][number]): string {
		const start = new Date(span.start + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
		if (span.status === 'finished') {
			const end = new Date(span.end + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
			return `${start} – ${end}`;
		}
		return `${start} – now`;
	}
</script>

<div class="settings-section" id="calendar-section">
	<div class="calendar-nav">
		<a class="iconbtn" aria-label="Previous month" href="/calendar?month={calendar.prev_month}">
			<svg viewBox="0 -960 960 960" fill="currentColor" aria-hidden="true">
				<path d="M560-240 320-480l240-240 56 56-184 184 184 184-56 56Z" />
			</svg>
		</a>
		<span class="calendar-month-label">{calendar.month_label}</span>
		<a class="iconbtn" aria-label="Next month" href="/calendar?month={calendar.next_month}">
			<svg viewBox="0 -960 960 960" fill="currentColor" aria-hidden="true">
				<path d="M504-480 320-664l56-56 240 240-240 240-56-56 184-184Z" />
			</svg>
		</a>
	</div>

	<!-- Same CSS-only radio toggle pattern as the spine/cover switch (see
	     (app)/+layout.svelte / app.css's #view-spine:checked) — grid and list both render below,
	     CSS shows whichever is checked. -->
	<input
		type="radio"
		name="calendar-view"
		id="calendar-view-grid"
		class="view-radio"
		value="grid"
		checked={auth.user?.calendar_view_preference !== 'list'}
		onchange={() => setCalendarView('grid')}
	/>
	<input
		type="radio"
		name="calendar-view"
		id="calendar-view-list"
		class="view-radio"
		value="list"
		checked={auth.user?.calendar_view_preference === 'list'}
		onchange={() => setCalendarView('list')}
	/>

	<div class="view-tabs calendar-view-tabs">
		<label for="calendar-view-grid" class="view-tab">GRID</label>
		<label for="calendar-view-list" class="view-tab">LIST</label>
	</div>

	<div class="calendar-grid-view">
		<div class="calendar-weekdays">
			<span>S</span><span>M</span><span>T</span><span>W</span><span>T</span><span>F</span><span>S</span>
		</div>
		{#each calendar.grid as row, rowIndex (rowIndex)}
			<div class="calendar-week">
				{#each row as cell, cellIndex (cell.date)}
					<CalendarDayCell
						{cell}
						prevCell={cellIndex > 0 ? row[cellIndex - 1] : null}
						nextCell={cellIndex < row.length - 1 ? row[cellIndex + 1] : null}
						{spansById}
					/>
				{/each}
			</div>
		{/each}
	</div>

	<div class="calendar-list-view">
		{#if calendar.spans.length}
			<div class="settings-list">
				{#each calendar.spans as span (span.entry_id)}
					<a href="/book/{span.entry_id}" class="settings-row calendar-list-row">
						<span class="calendar-list-swatch" style="background:{span.book.cover_color ?? paletteFor(span.book.id)}"></span>
						<span class="calendar-list-text">
							<span class="calendar-list-title">{span.book.title}</span>
							<span class="calendar-list-dates">{formatSpanRange(span)}</span>
						</span>
						<span class="settings-row-chevron"></span>
					</a>
				{/each}
			</div>
		{:else}
			<p class="empty-state">No reading activity this month.</p>
		{/if}
	</div>

	<div class="stats-tile-carousel">
		{#each calendar.tiles as tile (tile.label)}
			<div class="stats-tile">
				<span class="stats-tile-label">{tile.label}</span>
				<span class="stats-tile-value">{tile.value}</span>
				{#if tile.sub}<span class="stats-tile-sub">{tile.sub}</span>{/if}
			</div>
		{/each}
	</div>
</div>
