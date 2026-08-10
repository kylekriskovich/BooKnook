<script lang="ts">
	import { paletteFor } from '$lib/utils/palette';
	import { linksToDetail, type TBREntry } from '$lib/utils/entries';

	let { entry }: { entry: TBREntry } = $props();
</script>

{#snippet cover()}
	{#if entry.book.cover_url}
		<img src={entry.book.cover_url} alt={entry.book.title} class="cover-book-img" />
	{:else}
		<div class="cover-book-placeholder" style="background:{paletteFor(entry.book.id)}">
			<span class="cover-book-title">{entry.book.title}</span>
			{#if entry.book.author}<span class="cover-book-author">{entry.book.author}</span>{/if}
		</div>
	{/if}
{/snippet}

{#if linksToDetail(entry)}
	<a href="/book/{entry.id}" class="cover-book">
		{@render cover()}
	</a>
{:else}
	<button type="button" class="cover-book" popovertarget="confirm-{entry.id}">
		{@render cover()}
	</button>
{/if}
