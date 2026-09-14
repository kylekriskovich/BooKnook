import type { components } from '$lib/api/schema';

type BurndownPoint = components['schemas']['BurndownPointOut'];

export interface BurndownCoord {
	x: number;
	y: number;
}

/**
 * Maps burndown points onto SVG coordinates — x is proportional to elapsed days since the first
 * point, not index, so a gap between reading sessions shows as a gap rather than being smoothed
 * away by even spacing. y is remaining_percent flipped (0% remaining plots at the bottom), which
 * doubles as the progress-percent position the y-axis labels display. Kept client-side (pure
 * presentation) rather than server-computed.
 */
export function burndownSvgCoords(points: BurndownPoint[], width = 300, height = 100): BurndownCoord[] {
	if (points.length === 0) return [];
	if (points.length === 1) {
		const y = height * (1 - points[0].remaining_percent / 100);
		return [
			{ x: 0, y },
			{ x: width, y }
		];
	}
	const startDay = Date.parse(points[0].date);
	const totalDays = (Date.parse(points[points.length - 1].date) - startDay) / 86_400_000;
	return points.map((point) => {
		const x = (width * (Date.parse(point.date) - startDay)) / 86_400_000 / totalDays;
		const y = height * (1 - point.remaining_percent / 100);
		return { x, y };
	});
}

export function burndownSvgPoints(points: BurndownPoint[], width = 300, height = 100): string {
	return burndownSvgCoords(points, width, height)
		.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`)
		.join(' ');
}
