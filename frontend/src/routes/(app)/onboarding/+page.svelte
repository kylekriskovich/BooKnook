<script lang="ts">
	import { goto } from '$app/navigation';
	import { api, unwrap } from '$lib/api/client';
	import { auth } from '$lib/stores/auth.svelte';

	const year = new Date().getFullYear();
	let targetCount = $state('');
	let submitting = $state(false);

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		await finish();
	}

	async function skip() {
		await finish(true);
	}

	async function finish(skipGoal = false) {
		submitting = true;
		try {
			const parsed = !skipGoal && targetCount.trim() ? Number(targetCount) : undefined;
			const me = unwrap(
				await api.POST('/api/onboarding', {
					body: { target_count: parsed && parsed > 0 ? parsed : undefined }
				})
			);
			auth.set(me);
			await goto('/home');
		} finally {
			submitting = false;
		}
	}
</script>

<svelte:head>
	<title>Set a goal — Book Knook</title>
</svelte:head>

<h1>Set a reading goal for {year}?</h1>
<p class="empty-state">Track how many books you finish this year. You can change this later in Settings.</p>

<form class="settings-form" onsubmit={submit}>
	<label>
		Books to finish in {year}
		<input type="number" min="1" placeholder="e.g. 20" bind:value={targetCount} />
	</label>
	<button type="submit" class="btn btn-accent" disabled={submitting}>Set goal</button>
</form>

<form onsubmit={(e) => { e.preventDefault(); skip(); }}>
	<button type="submit" class="btn btn-ghost" disabled={submitting}>Skip for now</button>
</form>
