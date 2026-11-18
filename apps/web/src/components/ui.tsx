import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { ApiError } from "../lib/api";
import { humanise } from "../lib/format";

export function PageHead({ title, lede, actions }: { title: string; lede?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {lede && <p className="lede">{lede}</p>}
      </div>
      {actions && <div className="toolbar">{actions}</div>}
    </div>
  );
}

export function Panel({ title, meta, actions, children }: { title?: string; meta?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="panel">
      {(title || actions) && (
        <div className="panel-head">
          <div>
            {title && <h2>{title}</h2>}
            {meta && <div className="meta">{meta}</div>}
          </div>
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

export function Kpi({ label, value, unit, foot, alert, children }: {
  label: string; value: ReactNode; unit?: string; foot?: ReactNode; alert?: boolean; children?: ReactNode;
}) {
  return (
    <div className={`kpi${alert ? " alert" : ""}`}>
      <span className="label">{label}</span>
      <span className="value">
        {value}
        {unit && <small>{unit}</small>}
      </span>
      {children}
      {foot && <span className="foot">{foot}</span>}
    </div>
  );
}

export function Loading({ rows = 4 }: { rows?: number }) {
  return (
    <div aria-busy="true" aria-label="Loading" style={{ display: "grid", gap: 10 }}>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skeleton" style={{ height: 18, width: `${92 - i * 9}%` }} />
      ))}
    </div>
  );
}

export function ErrorState({ error }: { error: unknown }) {
  const status = error instanceof ApiError ? error.status : 0;
  const msg =
    status === 403
      ? "Your role doesn't have access to this."
      : status === 0
        ? "Can't reach the API. Is it running on port 8000?"
        : error instanceof Error
          ? error.message
          : "Something went wrong.";
  return <div className="error-box" role="alert">{msg}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

const STRONG = new Set(["qualified", "enterprise", "confirmed", "acknowledged", "In Progress", "active"]);
const DANGER = new Set(["p1", "urgent", "high"]);

export function Badge({ value, kind }: { value: string | null | undefined; kind?: "outline" }) {
  if (!value) return <span className="faint">–</span>;
  const cls = DANGER.has(value) ? "danger" : STRONG.has(value) ? "strong" : kind ?? "";
  const label = value === "p1" ? "P1" : humanise(value);
  return <span className={`badge ${cls}`}>{label}</span>;
}

export function Segmented<T extends string>({ value, options, onChange, label }: {
  value: T; options: { value: T; label: string; n?: number }[]; onChange: (v: T) => void; label: string;
}) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} aria-pressed={o.value === value} onClick={() => onChange(o.value)}>
          {o.label}
          {o.n !== undefined && <span className="n num">{o.n}</span>}
        </button>
      ))}
    </div>
  );
}

/** A search input whose value settles 300 ms after typing stops. */
export function useSearch(): [string, string, (v: string) => void] {
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setQ(text.trim()), 300);
    return () => clearTimeout(t);
  }, [text]);
  return [text, q, setText];
}

export function SearchBox({ value, onChange, placeholder, label }: {
  value: string; onChange: (v: string) => void; placeholder: string; label: string;
}) {
  return (
    <input className="input" type="search" placeholder={placeholder} value={value} aria-label={label} onChange={(e) => onChange(e.target.value)} />
  );
}
