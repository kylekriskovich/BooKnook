<script lang="ts">
	import ShelfRow from '$lib/components/ShelfRow.svelte';
	import AddSheet from '$lib/components/AddSheet.svelte';

	let { data } = $props();
</script>

<svelte:head>
	<title>My TBR — Book Knook</title>
</svelte:head>

<div id="shelves">
	{#each data.shelves as shelf (shelf.status)}
		<ShelfRow {shelf} />
	{/each}
</div>

<AddSheet />
