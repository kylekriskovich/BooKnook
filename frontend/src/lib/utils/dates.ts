// YYYY-MM-DD in the browser's local timezone, never toISOString() (UTC) - sent as the `today`
// query param so the server's UTC clock, which can lag a viewer's local day, isn't used instead.
export function localDateString(date: Date): string {
	const year = date.getFullYear();
	const month = String(date.getMonth() + 1).padStart(2, '0');
	const day = String(date.getDate()).padStart(2, '0');
	return `${year}-${month}-${day}`;
}

// Shared by PhysicalReadingSessionsSection and SessionLogModal - both render session rows.
export function formatDuration(startIso: string, endIso: string): string {
	const minutes = Math.round((Date.parse(endIso) - Date.parse(startIso)) / 60_000);
	const hours = Math.floor(minutes / 60);
	const mins = minutes % 60;
	if (hours && mins) return `${hours}h ${mins}m`;
	if (hours) return `${hours}h`;
	return `${mins}m`;
}

export function formatWhen(iso: string): string {
	return new Date(iso).toLocaleString(undefined, {
		month: 'short',
		day: 'numeric',
		hour: 'numeric',
		minute: '2-digit'
	});
}
