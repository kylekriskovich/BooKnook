<script lang="ts">
	import { goto } from '$app/navigation';
	import { api, unwrap, ApiError } from '$lib/api/client';
	import { auth } from '$lib/stores/auth.svelte';

	let username = $state('');
	let password = $state('');
	let error = $state<string | null>(null);
	let submitting = $state(false);

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		submitting = true;
		error = null;
		try {
			const me = unwrap(await api.POST('/api/login', { body: { username, password } }));
			auth.set(me);
			await goto(me.onboarded ? '/home' : '/onboarding');
		} catch (err) {
			error = err instanceof ApiError ? err.message : "Couldn't reach the server — try again.";
		} finally {
			submitting = false;
		}
	}
</script>

<svelte:head>
	<title>Log in — Book Knook</title>
</svelte:head>

<h1>Log in with Grimmory</h1>
{#if error}
	<p class="error">{error}</p>
{/if}
<form class="login-form" onsubmit={submit}>
	<input type="text" placeholder="Grimmory username" autocomplete="username" required bind:value={username} />
	<input type="password" placeholder="Password" autocomplete="current-password" required bind:value={password} />
	<button type="submit" disabled={submitting}>Log in</button>
</form>
