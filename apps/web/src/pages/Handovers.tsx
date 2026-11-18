import { useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { useAcknowledge, useAssignees, useHandovers, useReassign, useResolve, type HandoverRow as Row } from "../lib/api";
import { dateTime, humanise, relative } from "../lib/format";
import { Badge, Empty, ErrorState, Loading, PageHead, Panel, Segmented } from "../components/ui";

export default function Handovers() {
  const [status, setStatus] = useState<"open" | "acknowledged" | "resolved">("open");
  const { hasRole } = useAuth();
  const isManager = hasRole("admin", "manager");
  const q = useHandovers(status);
  const ack = useAcknowledge();
  const resolve = useResolve();
  const reassign = useReassign();
  const assignees = useAssignees(isManager);
  const [expanded, setExpanded] = useState<string | null>(null);
  const error = ack.error ?? resolve.error ?? reassign.error;

  return (
    <>
      <PageHead
        title="Handovers"
        lede={
          <>
            Conversations the assistant <b>stepped aside</b> from: pricing talks, complaints, urgent technical
            issues and security incidents. Each one has already been posted to Slack and emailed to the right person.
            {isManager
              ? " You can take any handover and pass it to someone else."
              : " Take one from the team queue or one assigned to you; a manager can reassign the others."}
          </>
        }
        actions={
          <Segmented
            label="Status"
            value={status}
            onChange={setStatus}
            options={[
              { value: "open", label: "Open" },
              { value: "acknowledged", label: "Taken" },
              { value: "resolved", label: "Resolved" },
            ]}
          />
        }
      />
      <Panel>
        {q.isLoading ? (
          <Loading rows={6} />
        ) : q.isError ? (
          <ErrorState error={q.error} />
        ) : !q.data?.length ? (
          <Empty>{status === "open" ? "Nobody is waiting for a person." : status === "acknowledged" ? "Nothing is being worked on right now." : "No resolved handovers yet."}</Empty>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Ref</th>
                  <th>Urgency</th>
                  <th>Reason</th>
                  <th>For</th>
                  <th>Raised</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {q.data.map((h) => (
                  <HandoverLine
                    key={h.id}
                    h={h}
                    open={expanded === h.id}
                    toggle={() => setExpanded(expanded === h.id ? null : h.id)}
                    onTake={() => ack.mutate(h.id)}
                    onResolve={() => resolve.mutate(h.id)}
                    onReassign={(to) => reassign.mutate({ id: h.id, to })}
                    busy={ack.isPending || resolve.isPending || reassign.isPending}
                    transcripts={isManager}
                    assignees={assignees.data ?? []}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
        {error && <div style={{ marginTop: 12 }}><ErrorState error={error} /></div>}
      </Panel>
    </>
  );
}

function Owner({ h }: { h: Row }) {
  if (h.status === "resolved")
    return <span className="small">Resolved{h.resolved_by_name ? ` by ${h.resolved_by_name}` : ""}<span className="sub">{h.resolved_at ? relative(h.resolved_at) : ""}</span></span>;
  if (h.status === "acknowledged")
    return <span className="small">Taken by <span className="strong">{h.acknowledged_by_name ?? "a colleague"}</span><span className="sub">{h.acknowledged_at ? relative(h.acknowledged_at) : ""}</span></span>;
  return h.assigned_to ? <span className="small">{h.assigned_to}</span> : <span className="small muted">Team queue</span>;
}

function HandoverLine({ h, open, toggle, onTake, onResolve, onReassign, busy, transcripts, assignees }: {
  h: Row;
  open: boolean;
  toggle: () => void;
  onTake: () => void;
  onResolve: () => void;
  onReassign: (to: string | null) => void;
  busy: boolean;
  transcripts: boolean;
  assignees: { name: string; email: string }[];
}) {
  const [to, setTo] = useState(h.assigned_to ?? "");
  const [other, setOther] = useState("");
  const OTHER = "__other__";
  const target = to === OTHER ? other.trim() : to;
  return (
    <>
      <tr className="clickable" onClick={toggle} aria-expanded={open}>
        <td className="strong num nowrap">{h.ref}</td>
        <td><Badge value={h.urgency} /></td>
        <td>
          {humanise(h.reason)}
          <span className="sub">{h.summary.slice(0, 90)}{h.summary.length > 90 ? "…" : ""}</span>
        </td>
        <td><Owner h={h} /></td>
        <td className="num small">
          {relative(h.created_at)}
          <span className="sub">{dateTime(h.created_at)}</span>
        </td>
        <td style={{ textAlign: "right" }} onClick={(e) => e.stopPropagation()}>
          <div className="toolbar" style={{ justifyContent: "flex-end" }}>
            {h.status === "open" &&
              (h.can_take ? (
                <button className="btn" disabled={busy} onClick={onTake}>
                  <span className="glyph">↪</span> I'll take it
                </button>
              ) : (
                <span className="small faint" title={`Assigned to ${h.assigned_to}`}>Needs a manager to reassign</span>
              ))}
            {h.can_resolve && h.status !== "open" && (
              <button className="btn" disabled={busy} onClick={onResolve}>
                <span className="glyph">↪</span> Mark resolved
              </button>
            )}
          </div>
        </td>
      </tr>
      {open && (
        <tr className="expand">
          <td colSpan={6}>
            <div className="detail">
              <span>{h.summary}</span>
              <span className="small muted">Notified: {h.route_to}</span>
              <div className="toolbar">
                {transcripts && (
                  <Link className="btn ghost" to={`/conversations/${h.conversation_id}`}>
                    Open transcript <span className="glyph">↪</span>
                  </Link>
                )}
                {h.can_resolve && h.status === "open" && (
                  <button className="btn ghost" disabled={busy} onClick={onResolve}>Mark resolved</button>
                )}
              </div>
              {h.can_reassign && (
                <form
                  className="toolbar"
                  onSubmit={(e) => {
                    e.preventDefault();
                    onReassign(target || null);
                  }}
                >
                  <label className="small muted" htmlFor={`to-${h.id}`}>Reassign to</label>
                  <select id={`to-${h.id}`} className="input" value={to} onChange={(e) => setTo(e.target.value)}>
                    <option value="">Team queue (anyone)</option>
                    {assignees.map((a) => (
                      <option key={a.email} value={a.email}>{a.name} · {a.email}</option>
                    ))}
                    {h.assigned_to && !assignees.some((a) => a.email === h.assigned_to) && (
                      <option value={h.assigned_to}>{h.assigned_to}</option>
                    )}
                    <option value={OTHER}>Someone else (sign-in email)…</option>
                  </select>
                  {to === OTHER && (
                    <>
                      <label className="sr-only" htmlFor={`other-${h.id}`}>Sign-in email</label>
                      <input
                        id={`other-${h.id}`}
                        className="input"
                        type="email"
                        placeholder="name@company"
                        value={other}
                        onChange={(e) => setOther(e.target.value)}
                      />
                    </>
                  )}
                  <button
                    className="btn"
                    disabled={busy || (to === OTHER && !other.includes("@")) || ((target || null) === (h.assigned_to ?? null) && h.status === "open")}
                  >
                    <span className="glyph">↪</span> Reassign
                  </button>
                </form>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
