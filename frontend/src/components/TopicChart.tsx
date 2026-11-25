import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { TopicStat, Topics } from "../api";
import { plural } from "../format";
import { COLORS, axisTick } from "./charts";

type TipProps = { active?: boolean; payload?: { payload: TopicStat }[] };

function TopicTooltip({ active, payload }: TipProps) {
  if (!active || !payload?.length) return null;
  const t = payload[0].payload;
  return (
    <div className="tooltip">
      <strong>{t.label}</strong>
      <div>
        Complaints: {plural(t.complaints, "review")} ({Math.round(t.complaint_share)}% of all reviews)
      </div>
      <div>Mentioned in {Math.round(t.share_of_negative_reviews)}% of negative reviews</div>
      <div>Praise: {plural(t.praise, "review")}</div>
      <div className="muted small">Click a bar to read these reviews</div>
    </div>
  );
}

type Props = { topics: Topics; onSelect: (topic: string, polarity: "positive" | "negative") => void };

export function TopicChart({ topics, onSelect }: Props) {
  const [asTable, setAsTable] = useState(false);
  if (!topics.review_count) return <p className="muted">No reviews in this period.</p>;
  const data = topics.topics;
  // Recharts passes the clicked bar's props; the row itself is in `payload`.
  const click = (polarity: "positive" | "negative") => (entry: { payload?: TopicStat }) => {
    if (entry.payload) onSelect(entry.payload.topic, polarity);
  };

  return (
    <>
      <div className="toolbar">
        <span className="muted small">Based on {plural(topics.review_count, "review")}. Click a bar to read them.</span>
        <button className="link small" onClick={() => setAsTable(!asTable)}>
          {asTable ? "Show as chart" : "Show as table"}
        </button>
      </div>
      {asTable ? (
        <div className="table-wrap">
          <table className="table compact">
            <thead>
              <tr>
                <th scope="col">Topic</th>
                <th scope="col">Complaints</th>
                <th scope="col">% of negative reviews</th>
                <th scope="col">Praise</th>
              </tr>
            </thead>
            <tbody>
              {data.map((t) => (
                <tr key={t.topic}>
                  <th scope="row">
                    <button className="link" onClick={() => onSelect(t.topic, "negative")}>{t.label}</button>
                  </th>
                  <td>{t.complaints}</td>
                  <td>{Math.round(t.share_of_negative_reviews)}%</td>
                  <td>{t.praise}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={data.length * 44 + 48}>
          <BarChart layout="vertical" data={data} margin={{ top: 0, right: 24, bottom: 0, left: 8 }}
                    barGap={2} barCategoryGap={10}>
            <CartesianGrid stroke={COLORS.grid} horizontal={false} />
            <XAxis type="number" allowDecimals={false} tick={axisTick} tickLine={false} axisLine={false} />
            <YAxis type="category" dataKey="label" width={150} tick={{ ...axisTick, fill: COLORS.text, fontSize: 13 }}
                   tickLine={false} axisLine={false} />
            <Tooltip content={<TopicTooltip />} cursor={{ fill: "rgba(0,0,0,0.04)" }} />
            <Legend verticalAlign="top" align="left" iconType="square" wrapperStyle={{ paddingBottom: 8 }}
                    formatter={(v: string) => <span style={{ color: COLORS.text }}>{v}</span>} />
            <Bar dataKey="complaints" name="Complaints (from 'disliked')" fill={COLORS.negative}
                 radius={[0, 4, 4, 0]} cursor="pointer" onClick={click("negative")} isAnimationActive={false} />
            <Bar dataKey="praise" name="Praise (from 'liked')" fill={COLORS.positive}
                 radius={[0, 4, 4, 0]} cursor="pointer" onClick={click("positive")} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      )}
    </>
  );
}
