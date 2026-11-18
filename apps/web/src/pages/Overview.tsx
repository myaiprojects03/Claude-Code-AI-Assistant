import { Link } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { useAcknowledge, useBookings, useHandovers, useOverview, useUnanswered } from "../lib/api";
import { compact, dateTime, humanise, pct, relative, shortDate } from "../lib/format";
import { BarChart } from "../components/BarChart";
import { Badge, Empty, ErrorState, Kpi, Loading, PageHead, Panel } from "../components/ui";

const FREE_DAILY_TOKENS = 400_000; // Groq free tier: 200k/day per model, two models

export default function Overview() {
  const { name, hasRole } = useAuth();
  const isManager = hasRole("admin", "manager");
  const unanswered = useUnanswered(isManager);
  const o = useOverview();
  const handovers = useHandovers("open");
  const bookings = useBookings("upcoming");
  const ack = useAcknowledge();

  if (o.isLoading) return <Loading rows={8} />;
  if (o.isError || !o.data) return <ErrorState error={o.error} />;
  const d = o.data;

  const delta = d.conversations_7d - d.conversations_prev_7d;
  const qualified = (d.leads_30d.qualified ?? 0) + (d.leads_30d.enterprise ?? 0);
  const leadsTotal = Object.values(d.leads_30d).reduce((a, b) => a + b, 0);
  const openHandovers = Object.values(d.handovers_open).reduce((a, b) => a + b, 0);
  const urgent = (d.handovers_open.p1 ?? 0) + (d.handovers_open.high ?? 0);
  const tokenShare = Math.min(1, d.llm_tokens_today / FREE_DAILY_TOKENS);
  const greeting = new Date().getHours() < 12 ? "Good morning" : new Date().getHours() < 18 ? "Good afternoon" : "Good evening";

  return (
    <>
      <PageHead
        title={`${greeting}${name ? `, ${name}` : ""}.`}
        lede={
          <>
            <b>{d.conversations_7d} conversations</b> this week, <b>{qualified} qualified leads</b> in the last 30
            days, and <b>{openHandovers === 0 ? "nobody" : `${openHandovers} ${openHandovers === 1 ? "person" : "people"}`}</b>{" "}
            waiting for a consultant.
          </>
        }
      />

      <div className="grid kpis">
        <Kpi
          label="Conversations, 7 days"
          value={compact(d.conversations_7d)}
          foot={
            <span className={`delta ${delta >= 0 ? "up" : "down"}`}>
              {delta >= 0 ? "+" : "−"}
              {Math.abs(delta)} vs previous 7 days
            </span>
          }
        />
        <Kpi
          label="Qualified rate, 30 days"
          value={pct(d.qualified_rate_30d)}
          foot={`${qualified} of ${leadsTotal} leads · ${d.leads_30d.new ?? 0} still qualifying`}
        />
        <Kpi label="Discovery calls, next 7 days" value={d.bookings_next_7d} foot={`${d.bookings_upcoming} upcoming in total`} />
        <Kpi
          label="Waiting for a person"
          value={openHandovers}
          alert={urgent > 0}
          foot={urgent > 0 ? `${urgent} urgent` : "No urgent handovers"}
        />
        <Kpi
          label="Model tokens today"
          value={compact(d.llm_tokens_today)}
          foot={`${d.llm_calls_today} calls · ${pct(tokenShare)} of the free daily allowance`}
        >
          <div className={`meter${tokenShare > 0.85 ? " high" : ""}`} role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(tokenShare * 100)} aria-label="Share of free daily token allowance used">
            <span style={{ width: `${Math.max(2, tokenShare * 100)}%` }} />
          </div>
        </Kpi>
      </div>

      <div className="grid two">
        <Panel
          title="Conversations per day"
          meta={
            <>
              Website chat, last 14 days, today highlighted · this week {d.guardrail_replacements_7d} replies guarded,{" "}
              {d.degraded_replies_7d} fell back
            </>
          }
        >
          <BarChart
            unit="conversations"
            caption="Conversations per day over the last 14 days"
            data={d.daily_conversations.map((p) => ({
              label: shortDate(p.date).split(" ")[0],
              full: shortDate(p.date),
              value: p.count,
            }))}
          />
        </Panel>

        <Panel
          title="Handover queue"
          meta="Most urgent first"
          actions={<Link className="btn ghost" to="/handovers">View all <span className="glyph">↪</span></Link>}
        >
          {handovers.isLoading ? (
            <Loading />
          ) : handovers.isError ? (
            <ErrorState error={handovers.error} />
          ) : !handovers.data?.length ? (
            <Empty>Nobody is waiting. The assistant is handling everything.</Empty>
          ) : (
            <div className="rows">
              {[...handovers.data]
                .sort((a, b) => rank(a.urgency) - rank(b.urgency))
                .slice(0, 5)
                .map((h) => (
                  <div className="row" key={h.id}>
                    <div>
                      <div className="title">
                        <Badge value={h.urgency} /> <span className="num">{h.ref}</span> · {humanise(h.reason)}
                      </div>
                      <div className="meta">
                        {h.assigned_to ? `For ${h.assigned_to} · ` : "Team queue · "}
                        {h.summary.slice(0, 110)}
                        {h.summary.length > 110 ? "…" : ""} · {relative(h.created_at)}
                      </div>
                    </div>
                    {h.can_take ? (
                      <button className="btn" disabled={ack.isPending} onClick={() => ack.mutate(h.id)}>
                        <span className="glyph">↪</span> I'll take it
                      </button>
                    ) : (
                      <span className="small faint">For {h.assigned_to}</span>
                    )}
                  </div>
                ))}
            </div>
          )}
        </Panel>
      </div>

      <div className="grid kpis three">
        <Kpi
          label="Booking conversion, 30 days"
          value={pct(d.booking_conversion_30d)}
          foot={`Qualified leads that booked a call · ${d.qualified_leads_30d} qualified`}
        />
        <Kpi
          label="Escalation rate, 30 days"
          value={pct(d.escalation_rate_30d)}
          foot={`Conversations handed to a person · of ${d.conversations_30d}`}
        />
        <Kpi
          label="Couldn't answer, 30 days"
          value={d.unanswered_30d}
          foot={isManager ? <Link to="/content">See the topics and add content ↪</Link> : "Questions the content didn't cover"}
        />
      </div>

      {isManager && (
        <Panel
          title="Topics the assistant couldn't answer"
          meta="Last 30 days, similar questions grouped · this is where content is missing"
          actions={<Link className="btn ghost" to="/content">Edit content <span className="glyph">↪</span></Link>}
        >
          {unanswered.isLoading ? (
            <Loading />
          ) : unanswered.isError ? (
            <ErrorState error={unanswered.error} />
          ) : !unanswered.data?.length ? (
            <Empty>Every question in the last 30 days was covered by the content.</Empty>
          ) : (
            <div className="rows">
              {unanswered.data.slice(0, 5).map((t) => (
                <div className="row" key={t.topic}>
                  <div>
                    <div className="title">{t.topic}</div>
                    <div className="meta">last asked {relative(t.last_asked)}</div>
                  </div>
                  <span className="badge strong num">{t.count}×</span>
                </div>
              ))}
            </div>
          )}
        </Panel>
      )}

      <Panel
        title="Upcoming discovery calls"
        actions={<Link className="btn ghost" to="/bookings">All bookings <span className="glyph">↪</span></Link>}
      >
        {bookings.isLoading ? (
          <Loading />
        ) : bookings.isError ? (
          <ErrorState error={bookings.error} />
        ) : !bookings.data?.length ? (
          <Empty>No calls booked yet.</Empty>
        ) : (
          <div className="rows">
            {bookings.data.slice(0, 5).map((b) => (
              <div className="row" key={b.id}>
                <div>
                  <div className="title">{b.contact_name ?? b.contact_email ?? "Visitor"}{b.company ? ` · ${b.company}` : ""}</div>
                  <div className="meta">{b.topic ?? "Discovery call"} · with {b.consultant}</div>
                </div>
                <div className="small muted num" style={{ textAlign: "right" }}>
                  {dateTime(b.starts_at)}
                  <div className="micro faint">{relative(b.starts_at)}</div>
                </div>
              </div>
            ))}
          </div>
        )}
      </Panel>
    </>
  );
}

function rank(u: string) {
  return { p1: 0, high: 1, normal: 2, low: 3 }[u] ?? 4;
}
