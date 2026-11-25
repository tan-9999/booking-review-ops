import type { ReactNode } from "react";

type Props = {
  title: string;
  subtitle?: string;
  loading?: boolean;
  error?: string;
  actions?: ReactNode;
  children?: ReactNode;
  id?: string;
};

/** Card with a heading, plus consistent loading and error states for every panel. */
export function Section({ title, subtitle, loading, error, actions, children, id }: Props) {
  return (
    <section className="card" id={id} aria-busy={loading}>
      <header className="card-header">
        <div>
          <h2>{title}</h2>
          {subtitle && <p className="muted">{subtitle}</p>}
        </div>
        {actions}
      </header>
      {error ? (
        <p className="error" role="alert">
          <span aria-hidden>⚠</span> Couldn't load this section. {error}
        </p>
      ) : (
        <div className={loading ? "loading" : undefined}>{children}</div>
      )}
    </section>
  );
}
