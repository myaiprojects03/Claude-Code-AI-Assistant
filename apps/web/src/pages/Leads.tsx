import { useState } from "react";
import { Link } from "react-router-dom";
import { useLeads } from "../lib/api";
import { humanise, relative } from "../lib/format";
import { Badge, Empty, ErrorState, Loading, PageHead, Panel, SearchBox, Segmented, useSearch } from "../components/ui";

type Status = "" | "qualified" | "enterprise" | "new" | "unqualified";

export default function Leads() {
  const [status, setStatus] = useState<Status>("");
  const [open, setOpen] = useState<string | null>(null);
  const [search, term, setSearch] = useSearch();
  const all = useLeads(undefined, term || undefined);
  const q = useLeads(status || undefined, term || undefined);
  const counts = (all.data ?? []).reduce<Record<string, number>>((acc, l) => ((acc[l.status] = (acc[l.status] ?? 0) + 1), acc), {});

  return (
    <>
      <PageHead
        title="Leads"
        lede={
          <>
            Prospects qualified by the assistant using the <b>rules in Settings</b>, not the model's judgement.
            Synced to the CRM once per change.
          </>
        }
        actions={
          <>
          <SearchBox value={search} onChange={setSearch} placeholder="Search name, email, company or need" label="Search leads" />
          <Segmented
            label="Lead status"
            value={status}
            onChange={setStatus}
            options={[
              { value: "", label: "All", n: all.data?.length },
              { value: "qualified", label: "Qualified", n: counts.qualified ?? 0 },
              { value: "enterprise", label: "Enterprise", n: counts.enterprise ?? 0 },
              { value: "new", label: "Qualifying", n: counts.new ?? 0 },
              { value: "unqualified", label: "Not a fit", n: counts.unqualified ?? 0 },
            ]}
          />
          </>
        }
      />
      <Panel>
        {q.isLoading ? (
          <Loading rows={8} />
        ) : q.isError ? (
          <ErrorState error={q.error} />
        ) : !q.data?.length ? (
          <Empty>{term ? `No leads match "${term}".` : "No leads here yet."}</Empty>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Contact</th>
                  <th>Need</th>
                  <th>Size</th>
                  <th>Budget</th>
                  <th>Timeline</th>
                  <th>Status</th>
                  <th>Created</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {q.data.map((l) => (
                  <LeadRows key={l.id} l={l} open={open === l.id} toggle={() => setOpen(open === l.id ? null : l.id)} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </>
  );
}

function LeadRows({ l, open, toggle }: { l: import("../lib/api").LeadRow; open: boolean; toggle: () => void }) {
  return (
    <>
      <tr className="clickable" onClick={toggle} aria-expanded={open}>
        <td>
          <span className="strong">{l.name ?? l.email ?? "Anonymous"}</span>
          <span className="sub">{[l.company, l.email && l.name ? l.email : null].filter(Boolean).join(" · ")}</span>
        </td>
        <td>
          {humanise(l.need_category) || <span className="faint">–</span>}
          {l.role && <span className="sub">{humanise(l.role)}</span>}
        </td>
        <td className="num">{l.company_size ?? <span className="faint">–</span>}</td>
        <td className="num">{l.budget_range ?? <span className="faint">–</span>}</td>
        <td className="num">{l.timeline ?? <span className="faint">–</span>}</td>
        <td><Badge value={l.status} /></td>
        <td className="small muted num">{relative(l.created_at)}</td>
        <td className="row-plus">{open ? "×" : "+"}</td>
      </tr>
      {open && (
        <tr className="expand">
          <td colSpan={8}>
            <div className="detail">
              {l.need && <span><span className="faint">In their words: </span>{l.need}</span>}
              {l.summary && <span className="small muted">{l.summary}</span>}
              {l.conversation_id && (
                <Link className="btn ghost" to={`/conversations/${l.conversation_id}`} style={{ justifySelf: "start" }}>
                  Open transcript <span className="glyph">↪</span>
                </Link>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
