// YYYY-MM-DD in the browser's own local timezone - never toISOString(), which is UTC. Sent as the
// `today` query param on every API route that needs to know "today" for something the user
// actually sees (calendar highlighting, book-detail's Estimated Completion, /api/stats' current
// year) instead of leaving it to the server's UTC clock — see app/main.py:_resolve_client_today
// for why that matters (the server's UTC day can lag a viewer's actual local day by up to many
// hours for anyone east of UTC).
export function localDateString(date: Date): string {
	const year = date.getFullYear();
	const month = String(date.getMonth() + 1).padStart(2, '0');
	const day = String(date.getDate()).padStart(2, '0');
	return `${year}-${month}-${day}`;
}
