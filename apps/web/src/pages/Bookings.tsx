import { useState } from "react";
import { useBookings } from "../lib/api";
import { dateTime, relative } from "../lib/format";
import { Badge, Empty, ErrorState, Loading, PageHead, Panel, SearchBox, Segmented, useSearch } from "../components/ui";

export default function Bookings() {
  const [scope, setScope] = useState<"upcoming" | "past">("upcoming");
  const [search, term, setSearch] = useSearch();
  const q = useBookings(scope, term || undefined);

  return (
    <>
      <PageHead
        title="Discovery calls"
        lede={
          <>
            Booked by the assistant. Clients get an <b>email</b> confirmation and a reminder 24 hours before; the
            consultant gets <b>Slack</b> at booking and 1 hour before.
          </>
        }
        actions={
          <>
          <SearchBox value={search} onChange={setSearch} placeholder="Search visitor, company, topic or consultant" label="Search bookings" />
          <Segmented
            label="When"
            value={scope}
            onChange={setScope}
            options={[
              { value: "upcoming", label: "Upcoming" },
              { value: "past", label: "Past" },
            ]}
          />
          </>
        }
      />
      <Panel>
        {q.isLoading ? (
          <Loading rows={6} />
        ) : q.isError ? (
          <ErrorState error={q.error} />
        ) : !q.data?.length ? (
          <Empty>{term ? `No calls match "${term}".` : scope === "upcoming" ? "No calls booked yet." : "No past calls."}</Empty>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>When (UK)</th>
                  <th>With</th>
                  <th>Consultant</th>
                  <th>Topic</th>
                  <th>Calendar</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {q.data.map((b) => (
                  <tr key={b.id}>
                    <td className="num">
                      <span className="strong">{dateTime(b.starts_at)}</span>
                      <span className="sub">London · {relative(b.starts_at)}</span>
                      {b.contact_timezone && b.contact_timezone !== "Europe/London" && (
                        <span className="sub">{visitorTime(b.starts_at, b.contact_timezone)} for the visitor</span>
                      )}
                    </td>
                    <td>
                      {b.contact_name ?? b.contact_email ?? "Visitor"}
                      <span className="sub">{[b.company, b.contact_name ? b.contact_email : null].filter(Boolean).join(" · ")}</span>
                    </td>
                    <td>{b.consultant}</td>
                    <td className="muted">{b.topic ?? "–"}</td>
                    <td className="small faint">{b.calendar_event_id ? b.calendar_event_id.split(":")[0] : "pending"}</td>
                    <td><Badge value={b.status} /></td>
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

/** The visitor picked the slot in their own timezone; show it beside the London time. */
function visitorTime(iso: string, tz: string) {
  try {
    const t = new Intl.DateTimeFormat("en-GB", { timeZone: tz, weekday: "short", hour: "2-digit", minute: "2-digit" }).format(new Date(iso));
    return `${t} ${tz.split("/").pop()?.replace(/_/g, " ")}`;
  } catch {
    return tz;
  }
}
