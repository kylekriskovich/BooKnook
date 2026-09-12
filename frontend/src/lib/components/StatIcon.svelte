<script lang="ts">
	// First-pass icon set for stat tiles (functionality/layout before final styling) - one icon
	// per stat_tiles.py label, with related labels sharing an icon where the stats are closely
	// related (Avg pages read/Avg book length; Avg finish time/Slowest finish).
	let { label }: { label: string } = $props();
</script>

{#if label === 'Books finished'}
	<svg viewBox="0 -960 960 960" fill="currentColor" aria-hidden="true">
		<path d="M382-240 154-468l57-57 171 171 356-356 57 57-413 413Z" />
	</svg>
{:else if label === 'Total pages read'}
	<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
		<rect x="3" y="4" width="8" height="16" rx="1" />
		<rect x="13" y="4" width="8" height="16" rx="1" />
	</svg>
{:else if label === 'Avg pages read' || label === 'Avg book length'}
	<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
		<rect x="4" y="13" width="4" height="7" />
		<rect x="10" y="8" width="4" height="12" />
		<rect x="16" y="3" width="4" height="17" />
	</svg>
{:else if label === 'Longest book'}
	<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
		<path d="M12 4l7 7h-4v9h-6v-9H5z" />
	</svg>
{:else if label === 'Shortest book'}
	<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
		<path d="M12 20l7-7h-4V4h-6v9H5z" />
	</svg>
{:else if label === 'Avg rating'}
	<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
		<polygon
			points="12,3 14.12,9.09 20.56,9.22 15.42,13.11 17.29,19.28 12,15.6 6.71,19.28 8.58,13.11 3.44,9.22 9.88,9.09"
		/>
	</svg>
{:else if label === 'Fastest finish'}
	<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
		<polygon points="13,2 4,14 11,14 9,22 20,9 12,9" />
	</svg>
{:else if label === 'Avg finish time' || label === 'Slowest finish'}
	<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">
		<circle cx="12" cy="13" r="8" />
		<path d="M12 9v4l3 2" stroke-linecap="round" stroke-linejoin="round" />
		<path d="M9 2h6" stroke-linecap="round" />
	</svg>
{/if}
