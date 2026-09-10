// YYYY-MM-DD in the browser's local timezone, never toISOString() (UTC) - sent as the `today`
// query param so the server's UTC clock, which can lag a viewer's local day, isn't used instead.
export function localDateString(date: Date): string {
	const year = date.getFullYear();
	const month = String(date.getMonth() + 1).padStart(2, '0');
	const day = String(date.getDate()).padStart(2, '0');
	return `${year}-${month}-${day}`;
}
