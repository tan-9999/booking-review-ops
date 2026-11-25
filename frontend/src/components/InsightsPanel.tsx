import type { Insight } from "../api";

const ICONS: Record<Insight["kind"], string> = {
  complaint: "⚠",
  change: "↗",
  strength: "★",
  data: "ℹ",
};

type Props = { insights: Insight[]; onTopic: (topic: string) => void };

export function InsightsPanel({ insights, onTopic }: Props) {
  if (!insights.length) return <p className="muted">No findings for this selection.</p>;
  return (
    <ul className="insights">
      {insights.map((insight, i) => {
        const icon = insight.kind === "change" && insight.text.includes(" fell ") ? "↘" : ICONS[insight.kind];
        return (
          <li key={i} className={`insight ${insight.kind} ${insight.severity}`}>
            <span className="insight-icon" aria-hidden>
              {icon}
            </span>
            <span>{insight.text}</span>
            {insight.topic && insight.kind !== "strength" && (
              <button className="link small" onClick={() => onTopic(insight.topic)}>
                Read these reviews →
              </button>
            )}
          </li>
        );
      })}
    </ul>
  );
}
