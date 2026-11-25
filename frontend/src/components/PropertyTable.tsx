import type { PropertyInfo, Summary } from "../api";
import { fmtDate, fmtRating, plural, timeAgo } from "../format";

function Freshness({ info }: { info?: PropertyInfo }) {
  if (!info) return null;
  if (info.freshness === "ok") {
    return <span className="badge good">✓ Updated {timeAgo(info.last_successful_scrape)}</span>;
  }
  if (info.freshness === "failing") {
    return (
      <span className="badge critical" title={info.last_run?.message}>
        ✕ Data collection failing
      </span>
    );
  }
  return (
    <span className="badge warning" title="No successful update in the last 48 hours">
      ⚠ Last updated {timeAgo(info.last_successful_scrape)}
    </span>
  );
}

type Props = {
  summary: Summary;
  properties: PropertyInfo[];
  selected: string;
  onSelect: (slug: string) => void;
};

export function PropertyTable({ summary, properties, selected, onSelect }: Props) {
  const info = Object.fromEntries(properties.map((p) => [p.slug, p]));
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th scope="col">Property</th>
            <th scope="col">Rating {summary.period.label}</th>
            <th scope="col">Change</th>
            <th scope="col">Last 12 months</th>
            <th scope="col">Data</th>
          </tr>
        </thead>
        <tbody>
          {summary.properties.map((p) => {
            const meta = info[p.slug];
            const change = p.rating_change;
            return (
              <tr key={p.slug} className={selected === p.slug ? "selected" : undefined}>
                <th scope="row">
                  <button className="link" onClick={() => onSelect(selected === p.slug ? "" : p.slug)}>
                    {p.name}
                  </button>
                </th>
                <td>
                  <strong>{fmtRating(p.current.average_rating)}</strong>{" "}
                  <span className={p.current.low_sample ? "caution" : "muted"}>
                    ({plural(p.current.review_count, "review")})
                  </span>
                </td>
                <td>
                  {change === null ? (
                    <span className="muted">–</span>
                  ) : (
                    <span className={`delta ${change > 0 ? "good" : change < 0 ? "bad" : "neutral"}`}>
                      {change > 0 ? "▲ +" : change < 0 ? "▼ " : "■ "}
                      {change.toFixed(1)}
                    </span>
                  )}
                </td>
                <td>
                  {fmtRating(meta?.average_rating ?? null)}{" "}
                  <span className="muted">({meta?.review_count ?? 0} reviews)</span>
                </td>
                <td>
                  <Freshness info={meta} />
                  {meta?.newest_review && <div className="muted small">Newest review {fmtDate(meta.newest_review)}</div>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
