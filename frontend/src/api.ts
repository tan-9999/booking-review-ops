// Typed client for the Django API (see backend/reviews/api/views.py).
import { useEffect, useState } from "react";

export type Stats = {
  average_rating: number | null;
  review_count: number;
  positive_share: number;
  negative_share: number;
  low_sample: boolean;
};
export type Comparison = { current: Stats; previous: Stats; rating_change: number | null };
export type Period = { start: string; end: string; label: string };
export type Summary = {
  period: Period;
  previous_period: Period;
  overall: Comparison;
  properties: (Comparison & { slug: string; name: string })[];
};
export type PropertyInfo = {
  slug: string;
  name: string;
  booking_url: string;
  review_count: number;
  average_rating: number | null;
  newest_review: string | null;
  last_successful_scrape: string | null;
  last_run: { status: string; started_at: string; message: string } | null;
  freshness: "ok" | "stale" | "failing";
};
export type TrendPoint = {
  period_start: string;
  average_rating: number | null;
  review_count: number;
  positive: number;
  mixed: number;
  negative: number;
  low_sample: boolean;
};
export type Trends = { granularity: "week" | "month"; start: string; end: string; points: TrendPoint[] };
export type TopicStat = {
  topic: string;
  label: string;
  complaints: number;
  praise: number;
  complaint_share: number;
  praise_share: number;
  share_of_negative_reviews: number;
};
export type Topics = { start: string; end: string; review_count: number; topics: TopicStat[] };
export type Insight = { kind: "complaint" | "change" | "strength" | "data"; severity: string; text: string; topic: string };
export type Insights = { period: Period; previous_period: Period; review_count: number; insights: Insight[] };
export type ReviewTopic = { topic: string; label: string; polarity: "positive" | "negative"; snippet: string };
export type Review = {
  id: number;
  property: string;
  property_name: string;
  review_date: string;
  rating: number;
  sentiment: "positive" | "mixed" | "negative" | "";
  title: string;
  positive_text: string;
  negative_text: string;
  hotel_response: string;
  reviewer_name: string;
  reviewer_country: string;
  traveller_type: string;
  room_type: string;
  nights: number | null;
  stay_date: string | null;
  topics: ReviewTopic[];
};
export type Page<T> = { count: number; next: string | null; previous: string | null; results: T[] };
export type Meta = {
  topics: { key: string; label: string }[];
  min_reviews_for_percentages: number;
  definitions: Record<string, string>;
};

type Params = Record<string, string | number | undefined>;

function query(params: Params): string {
  const pairs = Object.entries(params).filter(([, v]) => v !== undefined && v !== "");
  return new URLSearchParams(pairs.map(([k, v]) => [k, String(v)])).toString();
}

async function get<T>(path: string, params: Params = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/${path}/?${query(params)}`);
  } catch {
    throw new Error("Can't reach the server. Is the backend running (python manage.py runserver)?");
  }
  if (!response.ok) {
    let message = `The server returned an error (${response.status}).`;
    try {
      const body = await response.json();
      message = Object.values(body).flat().join(" ");
    } catch {
      /* keep the generic message */
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export type Scope = { property?: string; start?: string; end?: string };
export type FeedFilters = Scope & {
  sentiment?: string;
  topic?: string;
  polarity?: string;
  search?: string;
  ordering?: string;
};

export const api = {
  meta: () => get<Meta>("meta"),
  properties: () => get<PropertyInfo[]>("properties"),
  summary: (property?: string) => get<Summary>("summary", { property }),
  trends: (scope: Scope, granularity: "week" | "month") => get<Trends>("trends", { ...scope, granularity }),
  topics: (scope: Scope) => get<Topics>("topics", scope),
  insights: (scope: Scope) => get<Insights>("insights", scope),
  reviews: (filters: FeedFilters, page: number) => get<Page<Review>>("reviews", { ...filters, page, page_size: 10 }),
};

export type Loadable<T> = { data?: T; error?: string; loading: boolean };

/** Fetch on mount and whenever `deps` change; ignores responses from superseded requests. */
export function useApi<T>(load: () => Promise<T>, deps: unknown[]): Loadable<T> {
  const [state, setState] = useState<Loadable<T>>({ loading: true });
  useEffect(() => {
    let current = true;
    setState((s) => ({ data: s.data, loading: true }));
    load()
      .then((data) => current && setState({ data, loading: false }))
      .catch((e: Error) => current && setState({ error: e.message, loading: false }));
    return () => {
      current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}
