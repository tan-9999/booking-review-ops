import { useEffect, useState } from "react";
import { api, type FeedFilters, type Meta, type Review } from "../api";
import { plural } from "../format";
import { ReviewCard } from "./ReviewCard";

type Props = {
  scope: { property?: string; start?: string; end?: string };
  meta?: Meta;
  filters: FeedFilters;
  onChange: (filters: FeedFilters) => void;
};

export function ReviewFeed({ scope, meta, filters, onChange }: Props) {
  const [reviews, setReviews] = useState<Review[]>([]);
  const [count, setCount] = useState(0);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>();
  const [searchText, setSearchText] = useState(filters.search ?? "");

  const query = { ...scope, ...filters };
  const key = JSON.stringify(query);

  // Debounce typing in the search box.
  useEffect(() => {
    const t = setTimeout(() => {
      if ((filters.search ?? "") !== searchText) onChange({ ...filters, search: searchText || undefined });
    }, 350);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchText]);

  // Reset to page 1 whenever any filter changes.
  useEffect(() => {
    let current = true;
    setLoading(true);
    setError(undefined);
    api
      .reviews(query, 1)
      .then((data) => {
        if (!current) return;
        setReviews(data.results);
        setCount(data.count);
        setPage(1);
        setHasMore(Boolean(data.next));
      })
      .catch((e: Error) => current && setError(e.message))
      .finally(() => current && setLoading(false));
    return () => {
      current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const loadMore = () => {
    setLoading(true);
    api
      .reviews(query, page + 1)
      .then((data) => {
        setReviews((r) => [...r, ...data.results]);
        setPage(page + 1);
        setHasMore(Boolean(data.next));
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  };

  const set = (patch: Partial<FeedFilters>) => onChange({ ...filters, ...patch });
  const active = filters.sentiment || filters.topic || filters.search;

  return (
    <>
      <div className="filters" role="group" aria-label="Review filters">
        <label>
          Search
          <input type="search" placeholder="e.g. shower, wifi, check-in" value={searchText}
                 onChange={(e) => setSearchText(e.target.value)} />
        </label>
        <label>
          Overall
          <select value={filters.sentiment ?? ""} onChange={(e) => set({ sentiment: e.target.value || undefined })}>
            <option value="">All reviews</option>
            <option value="negative">Negative (below 6)</option>
            <option value="mixed">Mixed (6–7.9)</option>
            <option value="positive">Positive (8–10)</option>
          </select>
        </label>
        <label>
          Topic
          <select value={filters.topic ?? ""}
                  onChange={(e) => set({ topic: e.target.value || undefined, polarity: e.target.value ? filters.polarity : undefined })}>
            <option value="">Any topic</option>
            {meta?.topics.map((t) => (
              <option key={t.key} value={t.key}>{t.label}</option>
            ))}
          </select>
        </label>
        <label>
          Mentioned as
          <select value={filters.polarity ?? ""} disabled={!filters.topic}
                  onChange={(e) => set({ polarity: e.target.value || undefined })}>
            <option value="">Complaint or praise</option>
            <option value="negative">Complaint</option>
            <option value="positive">Praise</option>
          </select>
        </label>
        <label>
          Sort
          <select value={filters.ordering ?? "newest"} onChange={(e) => set({ ordering: e.target.value })}>
            <option value="newest">Newest first</option>
            <option value="oldest">Oldest first</option>
            <option value="lowest">Lowest score first</option>
            <option value="highest">Highest score first</option>
          </select>
        </label>
        {active && (
          <button className="link" onClick={() => { setSearchText(""); onChange({ ordering: filters.ordering }); }}>
            Clear filters
          </button>
        )}
      </div>

      {error ? (
        <p className="error" role="alert">⚠ {error}</p>
      ) : (
        <>
          <p className="muted" aria-live="polite">
            {loading && !reviews.length ? "Loading reviews…" : `${plural(count, "review")} match`}
          </p>
          <div className="reviews">
            {reviews.map((r) => (
              <ReviewCard key={r.id} review={r} />
            ))}
          </div>
          {!loading && !reviews.length && <p className="muted">No reviews match these filters.</p>}
          {hasMore && (
            <button className="button" onClick={loadMore} disabled={loading}>
              {loading ? "Loading…" : `Show more (${count - reviews.length} left)`}
            </button>
          )}
        </>
      )}
    </>
  );
}
