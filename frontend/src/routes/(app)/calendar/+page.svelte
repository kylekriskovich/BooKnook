<script lang="ts">
	import CalendarSection from '$lib/components/CalendarSection.svelte';

	let { data } = $props();
</script>

<svelte:head>
	<title>Reading Calendar — Book Knook</title>
</svelte:head>

<div class="list-header">
	<span class="list-title">Reading Calendar</span>
</div>

<CalendarSection calendar={data.calendar} />
