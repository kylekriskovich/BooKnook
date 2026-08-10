<script lang="ts">
	import { paletteFor } from '$lib/utils/palette';
	import type { TBREntry } from '$lib/utils/entries';

	let { entry }: { entry: TBREntry } = $props();
</script>

<div class="book-modal-body">
	{#if entry.book.cover_url}
		<img src={entry.book.cover_url} alt="" class="book-modal-cover" />
	{:else}
		<div class="book-modal-cover book-modal-cover-placeholder" style="background:{paletteFor(entry.book.id)}"></div>
	{/if}
	<div class="book-modal-info">
		<div class="book-modal-title">{entry.book.title}</div>
		{#if entry.owned !== null && entry.owned !== undefined}
			<span class="badge {entry.owned ? 'badge-owned' : 'badge-needed'}">
				{entry.owned ? 'In Library' : 'Requested'}
			</span>
		{/if}
		{#if entry.book.author}
			<div class="book-modal-meta">
				<svg viewBox="0 -960 960 960" fill="currentColor" aria-hidden="true">
					<path
						d="M367-527q-47-47-47-113t47-113q47-47 113-47t113 47q47 47 47 113t-47 113q-47 47-113 47t-113-47ZM160-160v-112q0-34 17.5-62.5T224-378q62-31 126-46.5T480-440q66 0 130 15.5T736-378q29 15 46.5 43.5T800-272v112H160Zm80-80h480v-32q0-11-5.5-20T700-306q-54-27-109-40.5T480-360q-56 0-111 13.5T260-306q-9 5-14.5 14t-5.5 20v32Zm296.5-343.5Q560-607 560-640t-23.5-56.5Q513-720 480-720t-56.5 23.5Q400-673 400-640t23.5 56.5Q447-560 480-560t56.5-23.5ZM480-640Zm0 400Z"
					/>
				</svg>
				<span>{entry.book.author}</span>
			</div>
		{/if}
		{#if entry.book.isbn}
			<div class="book-modal-meta">
				<svg viewBox="0 -960 960 960" fill="currentColor" aria-hidden="true">
					<path
						d="M200-80q-33 0-56.5-23.5T120-160v-451q-18-11-29-28.5T80-680v-120q0-33 23.5-56.5T160-880h640q33 0 56.5 23.5T880-800v120q0 23-11 40.5T840-611v451q0 33-23.5 56.5T760-80H200Zm0-520v440h560v-440H200Zm-40-80h640v-120H160v120Zm200 280h240v-80H360v80Zm120 20Z"
					/>
				</svg>
				<span>{entry.book.isbn}</span>
			</div>
		{/if}
		{#if entry.book.published_date}
			<div class="book-modal-meta">
				<svg viewBox="0 -960 960 960" fill="currentColor" aria-hidden="true">
					<path
						d="M200-80q-33 0-56.5-23.5T120-160v-560q0-33 23.5-56.5T200-800h40v-80h80v80h320v-80h80v80h40q33 0 56.5 23.5T840-720v560q0 33-23.5 56.5T760-80H200Zm0-80h560v-400H200v400Zm0-480h560v-80H200v80Zm0 0v-80 80Zm280 240q-17 0-28.5-11.5T440-440q0-17 11.5-28.5T480-480q17 0 28.5 11.5T520-440q0 17-11.5 28.5T480-400Zm-188.5-11.5Q280-423 280-440t11.5-28.5Q303-480 320-480t28.5 11.5Q360-457 360-440t-11.5 28.5Q337-400 320-400t-28.5-11.5ZM640-400q-17 0-28.5-11.5T600-440q0-17 11.5-28.5T640-480q17 0 28.5 11.5T680-440q0 17-11.5 28.5T640-400ZM480-240q-17 0-28.5-11.5T440-280q0-17 11.5-28.5T480-320q17 0 28.5 11.5T520-280q0 17-11.5 28.5T480-240Zm-188.5-11.5Q280-263 280-280t11.5-28.5Q303-320 320-320t28.5 11.5Q360-297 360-280t-11.5 28.5Q337-240 320-240t-28.5-11.5ZM640-240q-17 0-28.5-11.5T600-280q0-17 11.5-28.5T640-320q17 0 28.5 11.5T680-280q0 17-11.5 28.5T640-240Z"
					/>
				</svg>
				<span>{entry.book.published_date}</span>
			</div>
		{/if}
	</div>
</div>
