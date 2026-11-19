import { Fragment, useEffect, useState } from "react";
import { api } from "../lib/api";
import { dateTime } from "../lib/format";
import { Empty, ErrorState, Loading, PageHead, Panel, Segmented } from "../components/ui";

interface AuditRow {
  id: number;
  at: string;
  actor_type: string;
  actor_id: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  details: Record<string, unknown>;
}

const FILTERS = [
  { value: "", label: "All" },
  { value: "privacy.", label: "Privacy" },
  { value: "conversation.", label: "Transcripts" },
  { value: "leads.", label: "Leads" },
  { value: "internal_kb.", label: "Knowledge" },
  { value: "handover.", label: "Handovers" },
  { value: "config.", label: "Settings" },
  { value: "client_access.", label: "Access" },
];

export default function Audit() {
  const [action, setAction] = useState("");
  const [rows, setRows] = useState<AuditRow[] | null>(null);
  const [more, setMore] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [open, setOpen] = useState<number | null>(null);

  const load = async (before?: number) => {
    setError(null);
    try {
      const qs = new URLSearchParams({ limit: "100", ...(action ? { action } : {}), ...(before ? { before_id: String(before) } : {}) });
      const page = await api<AuditRow[]>(`/v1/dashboard/audit?${qs}`);
      setRows((prev) => (before && prev ? [...prev, ...page] : page));
      setMore(page.length === 100);
    } catch (e) {
      setError(e);
    }
  };

  useEffect(() => {
    setRows(null);
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [action]);

  return (
    <>
      <PageHead
        title="Audit log"
        lede={<>Who looked at or changed what: transcripts opened, lead lists viewed, settings published, access granted, privacy requests. Read-only; nothing can edit it.</>}
        actions={<Segmented label="Kind" value={action} onChange={setAction} options={FILTERS} />}
      />
      <Panel>
        {error ? (
          <ErrorState error={error} />
        ) : rows === null ? (
          <Loading rows={8} />
        ) : !rows.length ? (
          <Empty>Nothing recorded yet.</Empty>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Who</th>
                  <th>What</th>
                  <th>On</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <Fragment key={r.id}>
                    <tr className="clickable" onClick={() => setOpen(open === r.id ? null : r.id)} aria-expanded={open === r.id}>
                      <td className="num small nowrap">{dateTime(r.at)}</td>
                      <td className="small">
                        {typeof r.details.actor_name === "string" ? (
                          <span className="strong">{r.details.actor_name}</span>
                        ) : r.actor_id ? (
                          <span className="num">{r.actor_id.slice(0, 18)}</span>
                        ) : (
                          "–"
                        )}
                        <span className="sub">{r.actor_type}</span>
                      </td>
                      <td className="strong small">{r.action}</td>
                      <td className="small muted">
                        {r.target_type ?? ""} {r.target_id ? <span className="num">{r.target_id.slice(0, 24)}</span> : ""}
                      </td>
                    </tr>
                    {open === r.id && (
                      <tr className="expand">
                        <td colSpan={4}>
                          <pre className="small" style={{ margin: 0, whiteSpace: "pre-wrap" }}>{JSON.stringify(r.details, null, 2)}</pre>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {more && rows && (
          <div className="toolbar" style={{ marginTop: 12 }}>
            <button className="btn" onClick={() => void load(rows[rows.length - 1].id)}>Load older</button>
          </div>
        )}
      </Panel>
    </>
  );
}
