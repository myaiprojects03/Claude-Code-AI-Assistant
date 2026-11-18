import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { askInternal, useInternalChat, useInternalChats, type InternalTurn, type Source } from "../lib/api";
import { relative } from "../lib/format";
import { Empty, ErrorState, Loading, PageHead, Panel } from "../components/ui";

const EXAMPLES = [
  "What's the cutover plan for Northbridge's mailbox migration?",
  "Summarise our security incident SOP.",
  "Draft a follow-up email after the 21 October Northbridge meeting.",
];

export default function Assistant() {
  const qc = useQueryClient();
  const chats = useInternalChats();
  const [active, setActive] = useState<string | null>(null);
  const history = useInternalChat(active);
  const [turns, setTurns] = useState<InternalTurn[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [started, setStarted] = useState(0);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<unknown>(null);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (history.data) setTurns(history.data);
  }, [history.data]);
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(() => setElapsed(Math.round((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(t);
  }, [busy, started]);
  useEffect(() => {
    end.current?.scrollIntoView({ block: "end", behavior: "smooth" }); // may return a Promise: don't return it
  }, [turns.length, busy]);

  const send = async (question: string) => {
    const q = question.trim();
    if (!q || busy) return;
    setError(null);
    setText("");
    setTurns((t) => [...t, { role: "user", content: q, created_at: new Date().toISOString(), sources: [] }]);
    setBusy(true);
    setStarted(Date.now());
    setElapsed(0);
    try {
      const r = await askInternal(active, q);
      setTurns((t) => [...t, { role: "assistant", content: r.reply, created_at: new Date().toISOString(), sources: r.sources }]);
      if (!active) setActive(r.conversation_id);
      qc.invalidateQueries({ queryKey: ["internal-chats"] });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const newChat = () => {
    setActive(null);
    setTurns([]);
    setError(null);
  };

  return (
    <>
      <PageHead
        title="Knowledge assistant"
        lede={
          <>
            Ask about clients, projects and our methods, or have it draft a proposal, meeting summary or follow-up email.
            It answers <b>only from internal documents you're allowed to see</b>, cites them, and runs on a{" "}
            <b>local model</b>, so nothing leaves this machine. Answers take up to a minute; full drafts take longer.
          </>
        }
        actions={<button className="btn" onClick={newChat} disabled={busy}><span className="glyph">↪</span> New chat</button>}
      />
      <div className="grid assistant">
        <Panel title="Your chats" meta="Private to you">
          {chats.isLoading ? (
            <Loading rows={4} />
          ) : chats.isError ? (
            <ErrorState error={chats.error} />
          ) : !chats.data?.length ? (
            <Empty>No chats yet.</Empty>
          ) : (
            <div className="rows">
              {chats.data.map((c) => (
                <button
                  key={c.conversation_id}
                  className={`row chat-link${c.conversation_id === active ? " active" : ""}`}
                  onClick={() => !busy && setActive(c.conversation_id)}
                  aria-current={c.conversation_id === active}
                >
                  <span>
                    <span className="title small" style={{ display: "block" }}>{c.title}</span>
                    <span className="meta" style={{ display: "block" }}>{relative(c.created_at)} · {c.turns} {c.turns === 1 ? "question" : "questions"}</span>
                  </span>
                </button>
              ))}
            </div>
          )}
        </Panel>

        <Panel>
          <div className="transcript" aria-live="polite">
            {active && history.isLoading ? (
              <Loading rows={4} />
            ) : !turns.length ? (
              <div style={{ display: "grid", gap: 10, padding: "8px 0 12px" }}>
                <span className="micro faint">Try</span>
                {EXAMPLES.map((e) => (
                  <button key={e} className="btn" style={{ justifySelf: "start", whiteSpace: "normal", textAlign: "left" }} onClick={() => void send(e)}>
                    <span className="glyph">↪</span> {e}
                  </button>
                ))}
              </div>
            ) : (
              turns.map((t, i) => <Turn key={i} t={t} />)
            )}
            {busy && (
              <div className="msg-wrap assistant">
                <div className="msg assistant muted">Reading the documents… <span className="num">{elapsed}s</span></div>
                <span className="msg-meta">Local model · confidential documents stay on this machine</span>
              </div>
            )}
            <div ref={end} />
          </div>
          {error ? <ErrorState error={error} /> : null}
          <form
            className="composer"
            onSubmit={(e) => {
              e.preventDefault();
              void send(text);
            }}
          >
            <label className="sr-only" htmlFor="ask">Question</label>
            <textarea
              id="ask"
              className="input"
              placeholder="Ask a question or ask for a draft…"
              value={text}
              maxLength={4000}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send(text);
                }
              }}
            />
            <button className="btn primary" disabled={busy || !text.trim()}>
              <span className="glyph">↪</span> {busy ? "Working…" : "Ask"}
            </button>
          </form>
        </Panel>
      </div>
    </>
  );
}

function Turn({ t }: { t: InternalTurn }) {
  // "I can't find that..." answers still searched documents; say so instead of
  // presenting them as if they were the answer's sources.
  const notFound = /can.t find that|cannot find that/i.test(t.content);
  return (
    <div className={`msg-wrap ${t.role}`}>
      <div className={`msg ${t.role}`}>{t.content}</div>
      {t.role === "assistant" && t.sources.length > 0 && <Sources sources={t.sources} notFound={notFound} />}
      <span className="msg-meta">{t.role === "user" ? "You" : "Assistant"} · {relative(t.created_at)}</span>
    </div>
  );
}

function Sources({ sources, notFound }: { sources: Source[]; notFound: boolean }) {
  const [open, setOpen] = useState(false);
  if (notFound) {
    return (
      <div className="sources">
        <button className="btn ghost" style={{ justifySelf: "start", padding: "2px 4px" }} onClick={() => setOpen(!open)} aria-expanded={open}>
          Searched {sources.length} {sources.length === 1 ? "item" : "items"} you can access; none covered this {open ? "−" : "+"}
        </button>
        {open && <SourceList sources={sources} />}
      </div>
    );
  }
  return <SourceList sources={sources} />;
}

function SourceList({ sources }: { sources: Source[] }) {
  return (
    <ol className="sources" aria-label="Sources">
      {sources.map((s) => (
        <li key={s.n}>
          <span className="num faint">[{s.n}]</span>{" "}
          {s.kind === "record" ? (
            <>
              <span className="badge outline">Live record</span>{" "}
              <Link className="strong" to={s.uri ?? "/handovers"}>{s.title}</Link>
            </>
          ) : (
            <span className="strong">{s.title}</span>
          )}
          {s.section ? <span className="muted"> · {s.section}</span> : null}
          <span className="faint"> · {s.client ?? (s.kind === "record" ? "no client company" : "firm-wide")}</span>
        </li>
      ))}
    </ol>
  );
}
