import { useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TrendPoint, Trends } from "../api";
import { fmtMonth, fmtRating, fmtShortDate, plural } from "../format";
import { COLORS, axisTick } from "./charts";

type TipProps = { active?: boolean; payload?: { payload: TrendPoint }[]; fmt: (s: string) => string };

function PointTooltip({ active, payload, fmt }: TipProps) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="tooltip">
      <strong>{fmt(p.period_start)}</strong>
      <div>Average rating: {fmtRating(p.average_rating)}</div>
      <div>
        {plural(p.review_count, "review")}: {p.positive} positive · {p.mixed} mixed · {p.negative} negative
      </div>
      {p.low_sample && p.review_count > 0 && <div className="caution">Few reviews: the average is noisy</div>}
    </div>
  );
}

const legendText = (value: string) => <span style={{ color: COLORS.text }}>{value}</span>;

export function TrendCharts({ trends }: { trends: Trends }) {
  const [asTable, setAsTable] = useState(false);
  const fmt = trends.granularity === "month" ? fmtMonth : fmtShortDate;
  const unit = trends.granularity === "month" ? "Month" : "Week of";
  const points = trends.points;

  if (!points.some((p) => p.review_count)) return <p className="muted">No reviews in this period.</p>;

  return (
    <>
      <div className="toolbar">
        <button className="link small" onClick={() => setAsTable(!asTable)}>
          {asTable ? "Show as charts" : "Show as table"}
        </button>
      </div>
      {asTable ? (
        <div className="table-wrap">
          <table className="table compact">
            <thead>
              <tr>
                <th scope="col">{unit}</th>
                <th scope="col">Avg rating</th>
                <th scope="col">Reviews</th>
                <th scope="col">Positive</th>
                <th scope="col">Mixed</th>
                <th scope="col">Negative</th>
              </tr>
            </thead>
            <tbody>
              {points.map((p) => (
                <tr key={p.period_start}>
                  <th scope="row">{fmt(p.period_start)}</th>
                  <td>{fmtRating(p.average_rating)}</td>
                  <td>{p.review_count}</td>
                  <td>{p.positive}</td>
                  <td>{p.mixed}</td>
                  <td>{p.negative}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="chart-grid">
          <figure className="chart">
            <figcaption>Average rating (out of 10)</figcaption>
            <ResponsiveContainer width="100%" height={240}>
              <LineChart data={points} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
                <CartesianGrid stroke={COLORS.grid} vertical={false} />
                <XAxis dataKey="period_start" tickFormatter={fmt} tick={axisTick} tickLine={false}
                       axisLine={{ stroke: COLORS.grid }} minTickGap={24} />
                <YAxis domain={[0, 10]} ticks={[0, 2, 4, 6, 8, 10]} tick={axisTick} width={28}
                       tickLine={false} axisLine={false} />
                <Tooltip content={<PointTooltip fmt={fmt} />} cursor={{ stroke: COLORS.axis, strokeDasharray: "3 3" }} />
                <Line type="linear" dataKey="average_rating" name="Average rating" stroke={COLORS.line}
                      strokeWidth={2} connectNulls={false} isAnimationActive={false}
                      dot={{ r: 4, strokeWidth: 2, stroke: COLORS.surface, fill: COLORS.line }}
                      activeDot={{ r: 6, strokeWidth: 2, stroke: COLORS.surface }} />
              </LineChart>
            </ResponsiveContainer>
          </figure>
          <figure className="chart">
            <figcaption>Positive vs negative reviews</figcaption>
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={points} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
                <CartesianGrid stroke={COLORS.grid} vertical={false} />
                <XAxis dataKey="period_start" tickFormatter={fmt} tick={axisTick} tickLine={false}
                       axisLine={{ stroke: COLORS.grid }} minTickGap={24} />
                <YAxis allowDecimals={false} tick={axisTick} width={28} tickLine={false} axisLine={false} />
                <Tooltip content={<PointTooltip fmt={fmt} />} cursor={{ fill: "rgba(0,0,0,0.04)" }} />
                <Legend verticalAlign="top" align="left" iconType="square" wrapperStyle={{ paddingBottom: 8 }} formatter={legendText} />
                <Bar dataKey="positive" name="Positive (8–10)" stackId="s" fill={COLORS.positive}
                     stroke={COLORS.surface} strokeWidth={2} isAnimationActive={false} />
                <Bar dataKey="mixed" name="Mixed (6–7.9)" stackId="s" fill={COLORS.mixed}
                     stroke={COLORS.surface} strokeWidth={2} isAnimationActive={false} />
                <Bar dataKey="negative" name="Negative (below 6)" stackId="s" fill={COLORS.negative}
                     stroke={COLORS.surface} strokeWidth={2} radius={[4, 4, 0, 0]} isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          </figure>
        </div>
      )}
    </>
  );
}
