import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useConversations } from "../lib/api";
import { relative } from "../lib/format";
import { Badge, Empty, ErrorState, Loading, PageHead, Panel, Segmented } from "../components/ui";

type Status = "" | "active" | "handed_over" | "closed";

export default function Conversations() {
  const [status, setStatus] = useState<Status>("");
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const navigate = useNavigate();
  useEffect(() => {
    const t = setTimeout(() => setQ(search.trim()), 300);
    return () => clearTimeout(t);
  }, [search]);
  const list = useConversations({ status: status || undefined, q: q || undefined });

  return (
    <>
      <PageHead
        title="Conversations"
        lede={<>Every chat with the assistant, newest activity first. Opening a transcript is <b>logged</b> in the audit trail.</>}
        actions={
          <>
            <input
              className="input"
              type="search"
              placeholder="Search by name or email"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              aria-label="Search conversations"
            />
            <Segmented
              label="Status"
              value={status}
              onChange={setStatus}
              options={[
                { value: "", label: "All" },
                { value: "active", label: "Active" },
                { value: "handed_over", label: "Handed over" },
              ]}
            />
          </>
        }
      />
      <Panel>
        {list.isLoading ? (
          <Loading rows={8} />
        ) : list.isError ? (
          <ErrorState error={list.error} />
        ) : !list.data?.length ? (
          <Empty>No conversations match.</Empty>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Visitor</th>
                  <th>Last message</th>
                  <th>Lead</th>
                  <th>Status</th>
                  <th>Updated</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {list.data.map((c) => (
                  <tr
                    key={c.id}
                    className="clickable"
                    tabIndex={0}
                    onClick={() => navigate(`/conversations/${c.id}`)}
                    onKeyDown={(e) => e.key === "Enter" && navigate(`/conversations/${c.id}`)}
                  >
                    <td>
                      <span className="strong">{c.contact_name ?? c.contact_email ?? "Anonymous visitor"}</span>
                      <span className="sub">
                        {[c.company, c.channel === "web" ? "Website" : c.channel, c.verified ? "verified" : null]
                          .filter(Boolean)
                          .join(" · ")}
                      </span>
                    </td>
                    <td className="muted" style={{ maxWidth: 380 }}>
                      {c.last_message ?? <span className="faint">No messages</span>}
                      <span className="sub num">{c.messages} messages</span>
                    </td>
                    <td><Badge value={c.lead_status} /></td>
                    <td><Badge value={c.status} kind="outline" /></td>
                    <td className="small num muted">{relative(c.updated_at)}</td>
                    <td className="row-plus">+</td>
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
