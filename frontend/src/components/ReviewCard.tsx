import type { Review } from "../api";
import { fmtDate, fmtMonth } from "../format";

const SENTIMENT_LABEL: Record<string, string> = {
  positive: "Positive",
  mixed: "Mixed",
  negative: "Negative",
};

export function ReviewCard({ review }: { review: Review }) {
  const about = [
    review.reviewer_name,
    review.reviewer_country,
    review.traveller_type,
    review.room_type,
    review.nights ? `${review.nights} night${review.nights === 1 ? "" : "s"}` : "",
    review.stay_date ? `stayed ${fmtMonth(review.stay_date)}` : "",
  ].filter(Boolean);
  const complaints = review.topics.filter((t) => t.polarity === "negative");
  const praise = review.topics.filter((t) => t.polarity === "positive");

  return (
    <article className={`review ${review.sentiment}`}>
      <header className="review-header">
        <span className={`score ${review.sentiment}`} aria-label={`Score ${review.rating} out of 10`}>
          {review.rating.toFixed(1)}
        </span>
        <div className="review-heading">
          <h3>{review.title || <span className="muted">(no title)</span>}</h3>
          <p className="muted small">
            <strong>{review.property_name}</strong> · {fmtDate(review.review_date)} ·{" "}
            {SENTIMENT_LABEL[review.sentiment] ?? "Unscored"} review
          </p>
        </div>
      </header>

      {review.positive_text && (
        <p className="said liked">
          <span className="said-label">Liked</span> {review.positive_text}
        </p>
      )}
      {review.negative_text && (
        <p className="said disliked">
          <span className="said-label">Disliked</span> {review.negative_text}
        </p>
      )}
      {!review.positive_text && !review.negative_text && <p className="muted small">Score only, no written comments.</p>}

      {(complaints.length > 0 || praise.length > 0) && (
        <div className="chips" aria-label="Topics">
          {complaints.map((t) => (
            <span key={`n-${t.topic}`} className="chip complaint" title={t.snippet}>
              ✕ {t.label}
            </span>
          ))}
          {praise.map((t) => (
            <span key={`p-${t.topic}`} className="chip praise" title={t.snippet}>
              ✓ {t.label}
            </span>
          ))}
        </div>
      )}

      {review.hotel_response && (
        <details className="response">
          <summary>Property responded</summary>
          <p>{review.hotel_response}</p>
        </details>
      )}
      <p className="muted small">{about.join(" · ")}</p>
    </article>
  );
}
