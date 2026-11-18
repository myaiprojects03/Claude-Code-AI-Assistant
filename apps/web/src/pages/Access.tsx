import { useState } from "react";
import { useClientAccess, useGrant, useRevoke } from "../lib/api";
import { dateTime } from "../lib/format";
import { Empty, ErrorState, Loading, PageHead, Panel } from "../components/ui";

export default function Access() {
  const q = useClientAccess();
  const grant = useGrant();
  const revoke = useRevoke();
  const [email, setEmail] = useState("");
  const [company, setCompany] = useState("");

  const companies = q.data?.companies ?? [];
  const companyId = company || companies[0]?.id || "";

  return (
    <>
      <PageHead
        title="Client access"
        lede={
          <>
            Which clients' confidential documents each consultant can see in the knowledge assistant. Admins and
            managers see every client; consultants see <b>firm-wide documents plus the clients listed here</b>. Changes
            apply to their next question and are recorded in the audit log.
          </>
        }
      />
      <Panel title="Give a consultant access">
        <form
          className="toolbar"
          onSubmit={(e) => {
            e.preventDefault();
            if (!email.trim() || !companyId) return;
            grant.mutate({ staff_email: email.trim(), company_id: companyId }, { onSuccess: () => setEmail("") });
          }}
        >
          <label className="sr-only" htmlFor="grant-email">Consultant's sign-in email</label>
          <input
            id="grant-email"
            className="input"
            type="email"
            placeholder="Consultant's sign-in email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <label className="sr-only" htmlFor="grant-company">Client</label>
          <select id="grant-company" className="input" value={companyId} onChange={(e) => setCompany(e.target.value)}>
            {companies.map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
          <button className="btn primary" disabled={grant.isPending || !email.trim() || !companyId}>
            <span className="glyph">↪</span> {grant.isPending ? "Saving…" : "Give access"}
          </button>
        </form>
        {grant.isError && <div style={{ marginTop: 10 }}><ErrorState error={grant.error} /></div>}
      </Panel>

      <Panel title="Current access" meta={q.data ? `${q.data.grants.length} ${q.data.grants.length === 1 ? "assignment" : "assignments"}` : undefined}>
        {q.isLoading ? (
          <Loading />
        ) : q.isError ? (
          <ErrorState error={q.error} />
        ) : !q.data?.grants.length ? (
          <Empty>No consultant has access to any client yet. They only see firm-wide documents.</Empty>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Consultant</th>
                  <th>Client</th>
                  <th>Granted</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {q.data.grants.map((g) => (
                  <tr key={g.id}>
                    <td className="strong">{g.staff_email}</td>
                    <td>{g.company}</td>
                    <td className="small muted">
                      {dateTime(g.created_at)}
                      {g.granted_by && <span className="sub">by {g.granted_by}</span>}
                    </td>
                    <td style={{ textAlign: "right" }}>
                      <button
                        className="btn ghost"
                        disabled={revoke.isPending}
                        onClick={() => {
                          if (window.confirm(`Remove ${g.staff_email}'s access to ${g.company}?`)) revoke.mutate(g.id);
                        }}
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {revoke.isError && <ErrorState error={revoke.error} />}
      </Panel>
    </>
  );
}
