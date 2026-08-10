<script lang="ts">
	import AppChrome from '$lib/components/AppChrome.svelte';

	let { children } = $props();
</script>

<AppChrome>
	{@render children()}
</AppChrome>
