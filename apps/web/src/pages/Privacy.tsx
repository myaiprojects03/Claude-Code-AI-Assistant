import { useState } from "react";
import { api, apiDownload } from "../lib/api";
import { Empty, ErrorState, PageHead, Panel } from "../components/ui";

interface Summary {
  found: boolean;
  name: string | null;
  company: string | null;
  conversations: number;
  messages: number;
  leads: number;
  bookings: number;
  tickets: number;
  handovers: number;
  consents: number;
  on_do_not_contact: boolean;
  retention: Record<string, number>;
}

const RETENTION_LABELS: Record<string, string> = {
  conversation_days: "Website conversations (once nothing is open about them)",
  internal_chat_days: "Knowledge-assistant chats",
  code_days: "Verification-code records after they expire",
  outbox_days: "Records of sent emails and Slack messages",
  audit_days: "Audit log",
};

export default function Privacy() {
  const [email, setEmail] = useState("");
  const [summary, setSummary] = useState<Summary | null>(null);
  const [looked, setLooked] = useState("");
  const [confirm, setConfirm] = useState("");
  const [sure, setSure] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [erased, setErased] = useState<Record<string, number | boolean> | null>(null);

  const lookup = async (e?: string) => {
    const target = (e ?? email).trim();
    if (!target) return;
    setBusy(true);
    setError(null);
    setErased(null);
    try {
      setSummary(await api<Summary>(`/v1/dashboard/privacy?email=${encodeURIComponent(target)}`));
      setLooked(target);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  const doExport = async () => {
    setError(null);
    try {
      await apiDownload(`/v1/dashboard/privacy/export?email=${encodeURIComponent(looked)}`, "subject-access.json");
    } catch (err) {
      setError(err);
    }
  };

  const doErase = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api<Record<string, number | boolean>>("/v1/dashboard/privacy/erase", {
        method: "POST",
        body: JSON.stringify({ email: looked, confirm }),
      });
      setErased(r);
      setConfirm("");
      setSure(false);
      await lookup(looked);
      setErased(r);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  const total = summary
    ? summary.conversations + summary.leads + summary.bookings + summary.tickets + summary.handovers
    : 0;

  return (
    <>
      <PageHead
        title="Privacy requests"
        lede={
          <>
            Handle UK GDPR requests by email address: see what we hold, <b>export</b> it for the person, or{" "}
            <b>erase</b> it. Every lookup, export and erasure is recorded in the audit log.
          </>
        }
      />

      <Panel title="Find a person">
        <form
          className="toolbar"
          onSubmit={(e) => {
            e.preventDefault();
            void lookup();
          }}
        >
          <label className="sr-only" htmlFor="subject">Email address</label>
          <input id="subject" className="input" type="email" placeholder="their email address" value={email} onChange={(e) => setEmail(e.target.value)} />
          <button className="btn primary" disabled={busy || !email.trim()}>
            <span className="glyph">↪</span> {busy ? "Checking…" : "Look up"}
          </button>
        </form>
        {error ? <div style={{ marginTop: 12 }}><ErrorState error={error} /></div> : null}
      </Panel>

      {summary && (
        <div className="grid two">
          <Panel title={summary.found ? summary.name ?? looked : looked} meta={summary.company ?? (summary.found ? "Website visitor" : undefined)}>
            {!summary.found ? (
              <Empty>We hold nothing linked to this address.</Empty>
            ) : (
              <div className="rows">
                {(
                  [
                    ["Conversations", summary.conversations],
                    ["Messages", summary.messages],
                    ["Leads", summary.leads],
                    ["Discovery calls", summary.bookings],
                    ["Tickets raised", summary.tickets],
                    ["Handovers", summary.handovers],
                    ["Consent records", summary.consents],
                  ] as const
                ).map(([k, v]) => (
                  <div className="row" key={k}>
                    <span>{k}</span>
                    <span className="strong num">{v}</span>
                  </div>
                ))}
                <div className="row">
                  <span>On the do-not-contact list</span>
                  <span className="strong">{summary.on_do_not_contact ? "Yes" : "No"}</span>
                </div>
              </div>
            )}
            {summary.found && (
              <div className="toolbar" style={{ marginTop: 14 }}>
                <button className="btn" onClick={() => void doExport()}>
                  <span className="glyph">↪</span> Export their data (JSON)
                </button>
              </div>
            )}
          </Panel>

          <Panel title="Erase" meta="Right to erasure. This can't be undone.">
            {erased ? (
              <div className="small rows-tight">
                <span className="strong">Erased.</span>
                <span className="muted">
                  {Object.entries(erased)
                    .filter(([k, v]) => k !== "erased" && v)
                    .map(([k, v]) => `${k.replace(/_/g, " ")}: ${v}`)
                    .join(" · ") || "Nothing else to remove."}
                </span>
              </div>
            ) : !summary.found || total === 0 ? (
              <Empty>Nothing to erase.</Empty>
            ) : (
              <div style={{ display: "grid", gap: 12 }}>
                <p className="small muted">
                  Removes their name, email and phone, every chat message, lead answers and handover notes, and
                  cancels their future calls. Their address goes on the do-not-contact list. Tickets stay with their
                  company for the contract, without their name.
                </p>
                <label className="field">
                  <span className="field-label">Type their email again to confirm</span>
                  <input className="input" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
                </label>
                <label className="small" style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <input type="checkbox" checked={sure} onChange={(e) => setSure(e.target.checked)} /> I understand this can't
                  be undone
                </label>
                <button
                  className="btn primary"
                  style={{ justifySelf: "start" }}
                  disabled={busy || !sure || confirm.trim().toLowerCase() !== looked.trim().toLowerCase()}
                  onClick={() => void doErase()}
                >
                  <span className="glyph">↪</span> Erase everything about {looked}
                </button>
              </div>
            )}
          </Panel>
        </div>
      )}

      {summary && (
        <Panel title="Retention" meta="Applied automatically by the background worker every 6 hours">
          <div className="rows">
            {Object.entries(summary.retention).map(([k, days]) => (
              <div className="row" key={k}>
                <span>{RETENTION_LABELS[k] ?? k}</span>
                <span className="strong num">{days >= 365 && days % 365 === 0 ? `${days / 365} year${days === 365 ? "" : "s"}` : `${days} days`}</span>
              </div>
            ))}
          </div>
        </Panel>
      )}
    </>
  );
}
