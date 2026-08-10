<script lang="ts">
	import ShelfList from '$lib/components/ShelfList.svelte';
	import AddSheet from '$lib/components/AddSheet.svelte';

	let { data } = $props();
</script>

<svelte:head>
	<title>{data.shelf.label} — Book Knook</title>
</svelte:head>

<ShelfList status={data.shelf.status} label={data.shelf.label} entries={data.shelf.entries} />

<AddSheet />
