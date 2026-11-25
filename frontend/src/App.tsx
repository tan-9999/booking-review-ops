import { useState } from "react";
import { api, type FeedFilters, type Scope, useApi } from "./api";
import { addDays, fmtDate, iso, PRESETS, presetRange, type Preset } from "./format";
import { InsightsPanel } from "./components/InsightsPanel";
import { KpiCards } from "./components/KpiCards";
import { PropertyTable } from "./components/PropertyTable";
import { ReviewFeed } from "./components/ReviewFeed";
import { Section } from "./components/Section";
import { TopicChart } from "./components/TopicChart";
import { TrendCharts } from "./components/TrendCharts";

const daysBetween = (a: string, b: string) => (new Date(b).getTime() - new Date(a).getTime()) / 86_400_000;

export default function App() {
  const [property, setProperty] = useState("");
  const [preset, setPreset] = useState<Preset>("week");
  const [custom, setCustom] = useState({ start: "", end: "" });
  const [feed, setFeed] = useState<FeedFilters>({ ordering: "newest" });

  // ---- one shared scope for every panel below the headline numbers
  const customValid = Boolean(custom.start && custom.end && custom.start <= custom.end);
  const range = preset === "custom" ? (customValid ? custom : presetRange("4w")) : presetRange(preset);
  const prop = property || undefined;
  const scope: Scope = { property: prop, start: range.start, end: range.end };
  // "This week" is too short for a trend line: show the last 12 weeks for context.
  const trendScope: Scope =
    preset === "week" ? { property: prop, start: iso(addDays(new Date(), -7 * 12)), end: range.end } : scope;
  const granularity = daysBetween(trendScope.start!, trendScope.end!) > 120 ? "month" : "week";
  // "This week" insights use the backend's like-for-like week comparison.
  const insightScope: Scope = preset === "week" ? { property: prop } : scope;

  const meta = useApi(api.meta, []);
  const properties = useApi(api.properties, []);
  const summary = useApi(() => api.summary(prop), [prop]);
  const trends = useApi(() => api.trends(trendScope, granularity), [JSON.stringify(trendScope), granularity]);
  const topics = useApi(() => api.topics(scope), [JSON.stringify(scope)]);
  const insights = useApi(() => api.insights(insightScope), [JSON.stringify(insightScope)]);

  const showReviews = (topic: string, polarity: "positive" | "negative" = "negative") => {
    setFeed({ ...feed, topic, polarity, sentiment: undefined });
    document.getElementById("reviews")?.scrollIntoView({ behavior: "smooth" });
  };

  const failing = properties.data?.filter((p) => p.freshness !== "ok") ?? [];
  const periodText = summary.data
    ? `${fmtDate(summary.data.period.start)} – ${fmtDate(summary.data.period.end)}, compared with ${summary.data.previous_period.label}`
    : "";
  const rangeText = `${fmtDate(range.start)} – ${fmtDate(range.end)}`;
  const selectedName = properties.data?.find((p) => p.slug === property)?.name;

  return (
    <div className="page">
      <header className="topbar">
        <div>
          <h1>Guest reviews</h1>
          <p className="muted">Booking.com reviews for our properties, updated automatically.</p>
        </div>
        {properties.data && (
          <p className={failing.length ? "badge warning" : "badge good"} role="status">
            {failing.length
              ? `⚠ ${failing.length} propert${failing.length === 1 ? "y has" : "ies have"} out-of-date data`
              : "✓ All data up to date"}
          </p>
        )}
      </header>

      <div className="filterbar" role="group" aria-label="Dashboard filters">
        <label>
          Property
          <select value={property} onChange={(e) => setProperty(e.target.value)}>
            <option value="">All properties</option>
            {properties.data?.map((p) => (
              <option key={p.slug} value={p.slug}>{p.name}</option>
            ))}
          </select>
        </label>
        <div className="segmented" role="radiogroup" aria-label="Period">
          {PRESETS.map((p) => (
            <button key={p.key} role="radio" aria-checked={preset === p.key}
                    className={preset === p.key ? "active" : undefined} onClick={() => setPreset(p.key)}>
              {p.label}
            </button>
          ))}
        </div>
        {preset === "custom" && (
          <div className="dates">
            <label>
              From
              <input type="date" value={custom.start} max={custom.end || undefined}
                     onChange={(e) => setCustom({ ...custom, start: e.target.value })} />
            </label>
            <label>
              To
              <input type="date" value={custom.end} min={custom.start || undefined}
                     onChange={(e) => setCustom({ ...custom, end: e.target.value })} />
            </label>
            {!customValid && <span className="caution small">Pick both dates (showing last 4 weeks until then)</span>}
          </div>
        )}
      </div>

      <main className="grid">
        <Section title={selectedName ? `This week at ${selectedName}` : "This week at a glance"}
                 subtitle={periodText} loading={summary.loading} error={summary.error}>
          {summary.data && <KpiCards summary={summary.data} />}
        </Section>

        <Section title="What guests are saying"
                 subtitle={insights.data ? `${insights.data.review_count} reviews ${insights.data.period.label}, compared with ${insights.data.previous_period.label}` : undefined}
                 loading={insights.loading} error={insights.error}>
          {insights.data && <InsightsPanel insights={insights.data.insights} onTopic={(t) => showReviews(t)} />}
        </Section>

        <Section title="Properties" subtitle="Click a property to focus the whole dashboard on it."
                 loading={summary.loading || properties.loading} error={summary.error || properties.error}>
          {summary.data && properties.data && (
            <PropertyTable summary={summary.data} properties={properties.data} selected={property} onSelect={setProperty} />
          )}
        </Section>

        <Section title="Rating trends"
                 subtitle={preset === "week" ? "Last 12 weeks, for context" : rangeText}
                 loading={trends.loading} error={trends.error}>
          {trends.data && <TrendCharts trends={trends.data} />}
        </Section>

        <Section title="What reviews talk about" subtitle={`${rangeText}. Complaints come from the guest's "disliked" text, praise from "liked".`}
                 loading={topics.loading} error={topics.error}>
          {topics.data && <TopicChart topics={topics.data} onSelect={showReviews} />}
        </Section>

        <Section id="reviews" title="Reviews" subtitle={rangeText}>
          <ReviewFeed scope={scope} meta={meta.data} filters={feed} onChange={setFeed} />
        </Section>
      </main>

      <footer className="muted small">
        <p>
          <strong>How to read this:</strong> scores are Booking.com's 1–10 scale. Negative = below 6, mixed = 6–7.9,
          positive = 8 or more. "This week" runs Monday to today and is compared with the same days last week, so
          part-weeks are compared fairly. Numbers based on fewer than {meta.data?.min_reviews_for_percentages ?? 5} reviews
          are marked as uncertain.
        </p>
      </footer>
    </div>
  );
}
