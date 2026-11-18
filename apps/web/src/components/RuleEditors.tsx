import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { previewRules, useRuleOptions, type RuleOptions } from "../lib/api";
import { humanise } from "../lib/format";
import { Badge } from "./ui";

/** Settings that get a form instead of raw JSON (the API validates the same shapes). */
export const FORM_KEYS = new Set(["qualification.rules", "handover.rules", "booking.rules", "office.hours", "messaging.hours"]);

type Value = Record<string, unknown>;
type Props = { value: Value; onChange: (v: Value) => void; readOnly: boolean };

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const LABELS: Record<string, string> = {
  "<10k": "Under £10k", "10-50k": "£10k–50k", "50-150k": "£50k–150k", "150k+": "£150k+", unknown: "Not sure yet",
  "<1m": "Under a month", "1-3m": "1–3 months", "3-6m": "3–6 months", "6m+": "6 months+", exploring: "Just exploring",
  "1-10": "1–10 staff", "11-50": "11–50 staff", "51-250": "51–250 staff", "251-1000": "251–1,000 staff", "1000+": "1,000+ staff",
  need_category: "Need", company_size: "Company size", budget_range: "Budget", timeline: "Timeline", role: "Role",
  workplace: "Microsoft 365 / Workspace", data_analytics: "Data and analytics", it_strategy: "IT strategy",
};
const label = (v: string) => LABELS[v] ?? humanise(v);

export function RuleEditor({ ruleKey, ...p }: Props & { ruleKey: string }) {
  const opts = useRuleOptions();
  if (!opts.data) return <span className="small muted">Loading options…</span>;
  const o = opts.data;
  return (
    <div className="rule-editor">
      {ruleKey === "qualification.rules" && <Qualification {...p} o={o} />}
      {ruleKey === "handover.rules" && <HandoverRules {...p} o={o} />}
      {ruleKey === "office.hours" && <Hours {...p} withTimezone />}
      {ruleKey === "messaging.hours" && <Hours {...p} />}
      {ruleKey === "booking.rules" && <Booking {...p} />}
      <Preview ruleKey={ruleKey} value={p.value} o={o} />
    </div>
  );
}

// ---------- Building blocks ----------

function Field({ label: l, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="field">
      <span className="field-label">{l}</span>
      {hint && <span className="micro faint">{hint}</span>}
      {children}
    </div>
  );
}

function Chips({ options, value, onChange, readOnly, name }: {
  options: string[]; value: string[]; onChange: (v: string[]) => void; readOnly: boolean; name: string;
}) {
  return (
    <div className="chips" role="group" aria-label={name}>
      {options.map((opt) => {
        const on = value.includes(opt);
        return (
          <button
            key={opt}
            type="button"
            className={`chip${on ? " on" : ""}`}
            aria-pressed={on}
            disabled={readOnly}
            onClick={() => onChange(on ? value.filter((x) => x !== opt) : [...value, opt])}
          >
            {label(opt)}
          </button>
        );
      })}
    </div>
  );
}

function Tags({ value, onChange, readOnly, name, placeholder }: {
  value: string[]; onChange: (v: string[]) => void; readOnly: boolean; name: string; placeholder: string;
}) {
  const [draft, setDraft] = useState("");
  const add = () => {
    const t = draft.trim().toLowerCase();
    if (t && !value.includes(t)) onChange([...value, t]);
    setDraft("");
  };
  return (
    <div className="tags" aria-label={name}>
      {value.map((t) => (
        <span className="tag" key={t}>
          {t}
          {!readOnly && (
            <button type="button" aria-label={`Remove ${t}`} onClick={() => onChange(value.filter((x) => x !== t))}>×</button>
          )}
        </span>
      ))}
      {!readOnly && (
        <input
          className="input tag-input"
          placeholder={placeholder}
          value={draft}
          aria-label={`Add to ${name}`}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              add();
            }
          }}
          onBlur={add}
        />
      )}
    </div>
  );
}

const arr = (v: unknown) => (Array.isArray(v) ? (v as string[]) : []);
const str = (v: unknown) => (typeof v === "string" ? v : "");
const num = (v: unknown) => (typeof v === "number" ? v : 0);

// ---------- Qualification ----------

function Qualification({ value, onChange, readOnly, o }: Props & { o: RuleOptions }) {
  const set = (k: string) => (v: string[]) => onChange({ ...value, [k]: v });
  const c = (k: string, options: string[], name: string, hint?: string) => (
    <Field label={name} hint={hint}>
      <Chips name={name} options={options} value={arr(value[k])} onChange={set(k)} readOnly={readOnly} />
    </Field>
  );
  return (
    <div className="form-grid">
      {c("required", o.requirable, "Ask about before deciding", "The assistant collects these, one question at a time")}
      {c("qualifying_needs", o.needs, "Needs we qualify")}
      {c("min_sizes", o.sizes, "Company sizes that qualify")}
      {c("enterprise_sizes", o.sizes, "Treat as enterprise", "Always qualified and routed to an account manager")}
      {c("qualifying_budgets", o.budgets, "Budgets that qualify")}
      {c("unknown_budget_ok_for_roles", o.roles, "Budget 'not sure yet' is fine for", "Roles that can still qualify without a budget")}
      {c("qualifying_timelines", o.timelines, "Timelines that qualify")}
    </div>
  );
}

// ---------- Handover ----------

type Route = { slack: string; email: string; urgency: string };

function HandoverRules({ value, onChange, readOnly, o }: Props & { o: RuleOptions }) {
  const routes = (value.routes ?? {}) as Record<string, Route>;
  const setRoute = (reason: string, patch: Partial<Route>) =>
    onChange({ ...value, routes: { ...routes, [reason]: { ...routes[reason], ...patch } } });
  return (
    <div className="form-grid">
      <Field label="Security words" hint="Any of these in a visitor's message raises a P1 security handover at once, before the AI replies">
        <Tags name="Security words" placeholder="Add a word, then Enter" value={arr(value.security_keywords)} onChange={(v) => onChange({ ...value, security_keywords: v })} readOnly={readOnly} />
      </Field>
      <Field label="Not security" hint="Phrases that contain a security word but aren't incidents">
        <Tags name="Not security" placeholder="Add a phrase, then Enter" value={arr(value.security_exclusions)} onChange={(v) => onChange({ ...value, security_exclusions: v })} readOnly={readOnly} />
      </Field>
      <Field label="Where each kind of handover goes" hint="Enterprise clients go to their account manager instead (except security incidents)">
        <div className="table-wrap">
          <table className="data route-table">
            <thead>
              <tr><th>Reason</th><th>Slack channel</th><th>Email</th><th>Urgency</th></tr>
            </thead>
            <tbody>
              {o.handover_reasons.map((r) => (
                <tr key={r}>
                  <td className="strong small">{humanise(r)}</td>
                  <td>
                    <input className="input" aria-label={`${humanise(r)} Slack channel`} value={routes[r]?.slack ?? ""} readOnly={readOnly} onChange={(e) => setRoute(r, { slack: e.target.value })} />
                  </td>
                  <td>
                    <input className="input" type="email" aria-label={`${humanise(r)} email`} value={routes[r]?.email ?? ""} readOnly={readOnly} onChange={(e) => setRoute(r, { email: e.target.value })} />
                  </td>
                  <td>
                    <select className="input" aria-label={`${humanise(r)} urgency`} value={routes[r]?.urgency ?? "normal"} disabled={readOnly} onChange={(e) => setRoute(r, { urgency: e.target.value })}>
                      {o.urgencies.map((u) => <option key={u} value={u}>{u === "p1" ? "P1" : humanise(u)}</option>)}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Field>
    </div>
  );
}

// ---------- Hours and booking ----------

function DayPicker({ value, onChange, readOnly }: { value: number[]; onChange: (v: number[]) => void; readOnly: boolean }) {
  return (
    <div className="chips" role="group" aria-label="Days">
      {DAYS.map((d, i) => {
        const on = value.includes(i);
        return (
          <button key={d} type="button" className={`chip${on ? " on" : ""}`} aria-pressed={on} disabled={readOnly}
            onClick={() => onChange(on ? value.filter((x) => x !== i) : [...value, i].sort())}>
            {d}
          </button>
        );
      })}
    </div>
  );
}

function HoursFields({ value, onChange, readOnly, withTimezone }: Props & { withTimezone?: boolean }) {
  const set = (k: string, v: unknown) => onChange({ ...value, [k]: v });
  return (
    <>
      <Field label="Days">
        <DayPicker value={(value.days as number[]) ?? []} onChange={(v) => set("days", v)} readOnly={readOnly} />
      </Field>
      <div className="field-row">
        <Field label="From">
          <input className="input" type="time" value={str(value.start)} readOnly={readOnly} onChange={(e) => set("start", e.target.value)} />
        </Field>
        <Field label="Until">
          <input className="input" type="time" value={str(value.end)} readOnly={readOnly} onChange={(e) => set("end", e.target.value)} />
        </Field>
        {withTimezone && (
          <Field label="Timezone">
            <input className="input" value={str(value.timezone)} readOnly={readOnly} placeholder="Europe/London" onChange={(e) => set("timezone", e.target.value)} />
          </Field>
        )}
      </div>
      <div className="field-row">
        <Field label="Public holidays: country" hint="ISO code, e.g. GB. Empty = ignore holidays">
          <input className="input" value={str(value.holidays_country)} readOnly={readOnly} onChange={(e) => set("holidays_country", e.target.value.toUpperCase() || null)} />
        </Field>
        <Field label="Region" hint="e.g. ENG, SCT, WLS, NIR">
          <input className="input" value={str(value.holidays_subdiv)} readOnly={readOnly} onChange={(e) => set("holidays_subdiv", e.target.value.toUpperCase() || null)} />
        </Field>
      </div>
    </>
  );
}

function Hours(p: Props & { withTimezone?: boolean }) {
  return (
    <div className="form-grid">
      {!p.withTimezone && <span className="small muted">Applied in each client's own timezone. Reminders that fall outside are sent earlier, at the end of the last allowed window.</span>}
      <HoursFields {...p} />
    </div>
  );
}

function Booking(p: Props) {
  const { value, onChange, readOnly } = p;
  const n = (k: string, l: string, hint?: string, min = 1) => (
    <Field label={l} hint={hint}>
      <input className="input" type="number" min={min} value={num(value[k])} readOnly={readOnly}
        onChange={(e) => onChange({ ...value, [k]: e.target.value === "" ? 0 : Number(e.target.value) })} />
    </Field>
  );
  return (
    <div className="form-grid">
      <HoursFields {...p} withTimezone />
      <div className="field-row">
        {n("duration_min", "Call length (minutes)")}
        {n("step_min", "Start times every (minutes)")}
        {n("min_notice_hours", "Minimum notice (hours)", undefined, 0)}
      </div>
      <div className="field-row">
        {n("horizon_days", "Book up to (days ahead)")}
        {n("max_slots", "Times offered at once")}
        {n("max_per_day", "Most offered per day")}
      </div>
      <Field label="Consultant skill" hint="Only consultants with this skill take discovery calls">
        <input className="input" value={str(value.consultant_skill)} readOnly={readOnly} onChange={(e) => onChange({ ...value, consultant_skill: e.target.value })} />
      </Field>
    </div>
  );
}

// ---------- Preview ----------

function usePreview(ruleKey: string, value: Value, sample: Value) {
  const [state, setState] = useState<{ ok: boolean; result?: Record<string, unknown>; error?: string } | null>(null);
  const key = JSON.stringify([value, sample]);
  useEffect(() => {
    let live = true;
    const t = setTimeout(() => {
      previewRules(ruleKey, value, sample).then((r) => live && setState(r)).catch((e: Error) => live && setState({ ok: false, error: e.message }));
    }, 350);
    return () => {
      live = false;
      clearTimeout(t);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ruleKey, key]);
  return state;
}

function Preview({ ruleKey, value, o }: { ruleKey: string; value: Value; o: RuleOptions }) {
  const [sample, setSample] = useState<Value>(() =>
    ruleKey === "qualification.rules"
      ? { need_category: "managed_it", company_size: "51-250", budget_range: "10-50k", timeline: "1-3m", role: "decision_maker" }
      : ruleKey === "handover.rules"
        ? { text: "I think our files are encrypted by ransomware" }
        : ruleKey === "messaging.hours"
          ? { timezone: "Europe/London" }
          : {},
  );
  const p = usePreview(ruleKey, value, sample);
  const r = (p?.result ?? {}) as Record<string, unknown>;

  return (
    <div className="preview">
      <span className="field-label">Preview <span className="micro faint">· with these settings, before you save</span></span>
      {ruleKey === "qualification.rules" && (
        <div className="field-row">
          {(["need_category", "company_size", "budget_range", "timeline", "role"] as const).map((f) => {
            const options = { need_category: o.needs, company_size: o.sizes, budget_range: o.budgets, timeline: o.timelines, role: o.roles }[f];
            return (
              <Field key={f} label={label(f)}>
                <select className="input" value={str(sample[f])} onChange={(e) => setSample({ ...sample, [f]: e.target.value })}>
                  <option value="">Not given</option>
                  {options.map((x) => <option key={x} value={x}>{label(x)}</option>)}
                </select>
              </Field>
            );
          })}
        </div>
      )}
      {ruleKey === "handover.rules" && (
        <Field label="Try a visitor message">
          <input className="input" value={str(sample.text)} onChange={(e) => setSample({ text: e.target.value })} />
        </Field>
      )}
      {ruleKey === "messaging.hours" && (
        <Field label="Client's timezone">
          <input className="input" value={str(sample.timezone)} onChange={(e) => setSample({ timezone: e.target.value })} />
        </Field>
      )}

      {!p ? (
        <span className="small muted">Checking…</span>
      ) : !p.ok ? (
        <div className="error-box" role="alert">Can't be saved yet: {p.error}</div>
      ) : ruleKey === "qualification.rules" ? (
        <div className="small">
          This visitor would be marked <Badge value={str(r.status)} />
          {arr(r.missing).length > 0 && <> · still to ask: {arr(r.missing).map(label).join(", ")}</>}
          {arr(r.reasons).length > 0 && <> · because: {arr(r.reasons).join("; ")}</>}
        </div>
      ) : ruleKey === "handover.rules" ? (
        r.trigger ? (
          <div className="small">
            Raises a <span className="strong">{humanise(str(r.trigger))}</span> handover straight away →{" "}
            {str((r.route as Route | null)?.slack)}, {str((r.route as Route | null)?.email)}, <Badge value={str((r.route as Route | null)?.urgency)} />
          </div>
        ) : (
          <div className="small muted">No automatic handover. The assistant can still hand over if the conversation needs a person.</div>
        )
      ) : ruleKey === "office.hours" ? (
        <div className="small rows-tight">
          <span>Now: {str(r.now_local)} · <span className="strong">{r.open_now ? "open" : "closed"}</span></span>
          <span>A visitor who asks for a person now is told: <span className="strong">"{str(r.promise)}"</span></span>
          {arr(r.upcoming_closures).length > 0 && <span className="muted">Closed for holidays: {arr(r.upcoming_closures).join(" · ")}</span>}
        </div>
      ) : ruleKey === "messaging.hours" ? (
        <div className="small rows-tight">
          <span>Now for the client: {str(r.now_local)} · <span className="strong">{r.allowed_now ? "emails go out now" : "outside sending hours"}</span></span>
          {!r.allowed_now && <span>A reminder due right now would have gone out at <span className="strong">{str(r.reminder_due_now_sent_at)}</span>, the last allowed time before it.</span>}
          {arr(r.upcoming_closures).length > 0 && <span className="muted">No emails on: {arr(r.upcoming_closures).join(" · ")}</span>}
        </div>
      ) : (
        <div className="small rows-tight">
          <span>The assistant would offer these times (before checking consultants' calendars):</span>
          {arr(r.slots).length ? (
            <ul className="slot-list">{arr(r.slots).map((s) => <li key={s} className="num">{s}</li>)}</ul>
          ) : (
            <span className="strong">No times at all. Visitors couldn't book.</span>
          )}
        </div>
      )}
    </div>
  );
}
