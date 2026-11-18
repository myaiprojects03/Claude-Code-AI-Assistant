import { useState } from "react";
import { useTickets } from "../lib/api";
import { relative } from "../lib/format";
import { Badge, Empty, ErrorState, Loading, PageHead, Panel, Segmented } from "../components/ui";

export default function Tickets() {
  const [scope, setScope] = useState<"open" | "all">("open");
  const q = useTickets(scope === "open");
  const external = q.data?.filter((t) => t.synced !== null) ?? [];
  const syncNote = !external.length
    ? null
    : external.some((t) => t.synced === false)
      ? "Couldn't reach Jira for some tickets, so they show their last known status."
      : `Statuses checked live with Jira just now (${external.length} ${external.length === 1 ? "ticket" : "tickets"}).`;

  return (
    <>
      <PageHead
        title="Client tickets"
        lede={
          <>
            Raised by <b>verified clients</b> through the assistant, most urgent first. Urgent and high priority
            tickets also open a handover.
          </>
        }
        actions={
          <Segmented
            label="Tickets"
            value={scope}
            onChange={setScope}
            options={[
              { value: "open", label: "Open" },
              { value: "all", label: "All" },
            ]}
          />
        }
      />
      <Panel>
        {syncNote && <p className="micro faint" style={{ marginBottom: 10 }}>{syncNote}</p>}
        {q.isLoading ? (
          <Loading rows={6} />
        ) : q.isError ? (
          <ErrorState error={q.error} />
        ) : !q.data?.length ? (
          <Empty>No {scope === "open" ? "open " : ""}tickets.</Empty>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Ref</th>
                  <th>Summary</th>
                  <th>Client</th>
                  <th>Priority</th>
                  <th>Status</th>
                  <th>Updated</th>
                </tr>
              </thead>
              <tbody>
                {q.data.map((t) => (
                  <tr key={t.id}>
                    <td className="strong num nowrap">
                      {t.ref}
                      {t.external_id && <span className="sub">{t.external_id}</span>}
                    </td>
                    <td>
                      {t.summary}
                      <span className="sub num">{t.updates} update{t.updates === 1 ? "" : "s"}</span>
                    </td>
                    <td>
                      {t.company}
                      {t.contact && <span className="sub">{t.contact}</span>}
                    </td>
                    <td><Badge value={t.priority} /></td>
                    <td><Badge value={t.status} kind="outline" /></td>
                    <td className="small muted num">{relative(t.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </>
  );
}
