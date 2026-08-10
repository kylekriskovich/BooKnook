<script lang="ts">
	import { api, unwrap } from '$lib/api/client';
	import { auth } from '$lib/stores/auth.svelte';

	let { grimmoryAdminConfigured, spiceLabels }: { grimmoryAdminConfigured: boolean; spiceLabels: string[] } = $props();

	let error = $state<string | null>(null);
	let saving = $state(false);

	async function setLevel(level: number) {
		saving = true;
		error = null;
		try {
			const result = unwrap(await api.POST('/api/settings/spice', { body: { level } }));
			if (auth.user) auth.user.spice_level = result.spice_level;
			error = result.error ?? null;
		} finally {
			saving = false;
		}
	}
</script>

<div class="settings-section" id="spice-section">
	<div class="settings-section-title">Content filtering</div>
	{#if !grimmoryAdminConfigured}
		<p class="empty-state">Not set up yet — ask your admin to configure Grimmory admin access first (/admin/settings).</p>
	{:else}
		{#if error}<p class="error">{error}</p>{/if}
		<p class="empty-state">Level of Spicy Content</p>
		<div class="chili-picker">
			{#each spiceLabels as label, i (i)}
				<button
					type="button"
					class="chili-btn{i <= (auth.user?.spice_level ?? 0) ? '' : ' chili-dim'}"
					disabled={saving}
					aria-label={label}
					aria-pressed={i === auth.user?.spice_level}
					onclick={() => setLevel(i)}
				>
					🌶️
				</button>
			{/each}
		</div>
		<p class="empty-state">{spiceLabels[auth.user?.spice_level ?? 0]}</p>
	{/if}
</div>
