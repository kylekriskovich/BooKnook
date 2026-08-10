<script lang="ts">
	import { paletteFor } from '$lib/utils/palette';
	import { linksToDetail, type TBREntry } from '$lib/utils/entries';

	let { entry }: { entry: TBREntry } = $props();
</script>

{#if linksToDetail(entry)}
	<a href="/book/{entry.id}" class="spine-book" style="background:{paletteFor(entry.book.id)}">
		<span class="spine-title">{entry.book.title}</span>
	</a>
{:else}
	<button type="button" class="spine-book" popovertarget="confirm-{entry.id}" style="background:{paletteFor(entry.book.id)}">
		<span class="spine-title">{entry.book.title}</span>
	</button>
{/if}
