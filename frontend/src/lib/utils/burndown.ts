import type { components } from '$lib/api/schema';

type BurndownPoint = components['schemas']['BurndownPointOut'];

/**
 * Maps burndown points onto an SVG viewBox of the given size — mirrors
 * app/stat_tiles.py:burndown_svg_points exactly (x proportional to each point's actual elapsed
 * days since the first point, not its index, so a gap between reading sessions shows up as a gap
 * on the chart instead of being smoothed away by even spacing). Kept client-side rather than
 * server-computed since it's pure presentation, not data — see app/schemas.py's BookDetailOut,
 * which only sends the raw points.
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
