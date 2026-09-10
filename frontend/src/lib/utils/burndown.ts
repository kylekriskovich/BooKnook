import type { components } from '$lib/api/schema';

type BurndownPoint = components['schemas']['BurndownPointOut'];

/**
 * Maps burndown points onto an SVG viewBox — x is proportional to elapsed days since the first
 * point, not index, so a gap between reading sessions shows as a gap rather than being smoothed
 * away by even spacing. Kept client-side (pure presentation) rather than server-computed.
 */
export function burndownSvgPoints(points: BurndownPoint[], width = 300, height = 100): string {
	if (points.length === 0) return '';
	if (points.length === 1) {
		const y = height * (1 - points[0].remaining_percent / 100);
		return `0,${y.toFixed(1)} ${width},${y.toFixed(1)}`;
	}
	const startDay = Date.parse(points[0].date);
	const totalDays = (Date.parse(points[points.length - 1].date) - startDay) / 86_400_000;
	return points
		.map((point) => {
			const x = (width * (Date.parse(point.date) - startDay)) / 86_400_000 / totalDays;
			const y = height * (1 - point.remaining_percent / 100);
			return `${x.toFixed(1)},${y.toFixed(1)}`;
		})
		.join(' ');
}
