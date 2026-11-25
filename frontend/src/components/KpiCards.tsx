import type { Summary } from "../api";
import { fmtRating, plural } from "../format";

// higherIsBetter: true/false colours the change good/bad; null = neutral (e.g. review volume)
type DeltaProps = { value: number | null; digits?: number; suffix?: string; higherIsBetter: boolean | null };

/** Change vs the comparison period: arrow + sign + words, so it never relies on colour alone. */
function Delta({ value, digits = 1, suffix = "", higherIsBetter }: DeltaProps) {
  if (value === null) return <span className="delta neutral">No comparison yet</span>;
  if (Math.abs(value) < 10 ** -digits / 2) return <span className="delta neutral">■ No change</span>;
  const tone = higherIsBetter === null ? "neutral" : value > 0 === higherIsBetter ? "good" : "bad";
  return (
    <span className={`delta ${tone}`}>
      {value > 0 ? "▲" : "▼"} {value > 0 ? "+" : ""}
      {value.toFixed(digits)}
      {suffix}
    </span>
  );
}

export function KpiCards({ summary }: { summary: Summary }) {
  const { current, previous, rating_change } = summary.overall;
  const countChange = current.review_count - previous.review_count;
  const negChange =
    current.review_count && previous.review_count ? current.negative_share - previous.negative_share : null;
  const vs = `vs ${summary.previous_period.label}`;

  return (
    <div className="kpis">
      <div className="kpi accent-rating">
        <span className="kpi-label">Average rating</span>
        <span className="kpi-value">
          {fmtRating(current.average_rating)}
          <small>/10</small>
        </span>
        <span>
          <Delta value={rating_change} higherIsBetter /> <span className="muted">{vs}</span>
        </span>
        {current.low_sample && (
          <span className="caution">Based on only {plural(current.review_count, "review")}: treat with caution</span>
        )}
      </div>
      <div className="kpi accent-volume">
        <span className="kpi-label">Reviews received</span>
        <span className="kpi-value">{current.review_count}</span>
        <span>
          <Delta value={countChange} digits={0} higherIsBetter={null} /> <span className="muted">{vs}</span>
        </span>
      </div>
      <div className="kpi accent-negative">
        <span className="kpi-label">Negative reviews (score below 6)</span>
        <span className="kpi-value">
          {current.review_count ? Math.round(current.negative_share) : "–"}
          <small>%</small>
        </span>
        <span>
          <Delta value={negChange} digits={0} suffix=" pts" higherIsBetter={false} /> <span className="muted">{vs}</span>
        </span>
      </div>
    </div>
  );
}
