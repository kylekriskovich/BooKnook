// Spine/cover placeholder background gradients, keyed by book id % length — shared across every
// shelf view so a given book always gets the same color. Mirrors app/main.py's
// templates.env.globals["palette"] (same 7 values, same ordering) — keep the two in sync if this
// ever changes, until app/templates/ is deleted in the Phase C cutover.
export const PALETTE = [
	'linear-gradient(160deg,#2b4570,#16233a)',
	'linear-gradient(160deg,#7a2e2e,#3f1414)',
	'linear-gradient(160deg,#2e5c4a,#153224)',
	'linear-gradient(160deg,#6b4a1f,#38260e)',
	'linear-gradient(160deg,#5c3a6b,#2c1a34)',
	'linear-gradient(160deg,#1f5266,#0e2833)',
	'linear-gradient(160deg,#7a4a2e,#3f2414)'
];

export function paletteFor(bookId: number): string {
	return PALETTE[bookId % PALETTE.length];
}
