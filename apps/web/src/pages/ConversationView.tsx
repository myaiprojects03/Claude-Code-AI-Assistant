import { Link, useParams } from "react-router-dom";
import { useConversation, type MessageOut } from "../lib/api";
import { dateTime } from "../lib/format";
import { Badge, ErrorState, Loading, PageHead, Panel } from "../components/ui";

export default function ConversationView() {
  const { id = "" } = useParams();
  const q = useConversation(id);

  if (q.isLoading) return <Loading rows={10} />;
  if (q.isError || !q.data) return <ErrorState error={q.error} />;
  const c = q.data;
  const toolResults = new Map(
    c.messages.filter((m) => m.role === "tool").map((m) => [m.tool_name + ":" + m.id, m]),
  );

  return (
    <>
      <PageHead
        title="Transcript"
        lede={
          <>
            Started <b>{dateTime(c.created_at)}</b> · {c.channel === "web" ? "website chat" : c.channel} ·{" "}
            {c.verified ? "identity verified" : "not verified"}
          </>
        }
        actions={
          <>
            <Badge value={c.status} kind="outline" />
            <Link className="btn ghost" to="/conversations">
              <span className="glyph">↩</span> All conversations
            </Link>
          </>
        }
      />
      <Panel title={plural(c.messages.filter((m) => m.role === "user").length, "visitor message")} meta="Tool calls are collapsed; open one to see exactly what the assistant did.">
        <div className="transcript">
          {c.messages.map((m) => (
            <Message key={m.id} m={m} results={toolResults} />
          ))}
        </div>
      </Panel>
    </>
  );
}

function Message({ m }: { m: MessageOut; results: Map<string, MessageOut> }) {
  if (m.role === "system") return null;
  if (m.role === "tool") {
    let pretty = m.content ?? "";
    try {
      pretty = JSON.stringify(JSON.parse(pretty), null, 2);
    } catch {
      /* not JSON */
    }
    const failed = pretty.includes('"error"');
    return (
      <details className="tool">
        <summary>
          <span>
            Result · <span className="strong">{m.tool_name}</span>
            {failed ? <span style={{ color: "var(--danger)" }}> · refused</span> : null}
          </span>
        </summary>
        <pre>{pretty}</pre>
      </details>
    );
  }
  if (m.role === "assistant" && m.tool_calls?.length) {
    return (
      <>
        {m.content && <Bubble m={m} />}
        {m.tool_calls.map((t) => (
          <details className="tool" key={t.id}>
            <summary>
              <span>
                Called <span className="strong">{t.name}</span>
              </span>
            </summary>
            <pre>{JSON.stringify(t.arguments, null, 2)}</pre>
          </details>
        ))}
      </>
    );
  }
  return <Bubble m={m} />;
}

function plural(n: number, word: string) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

function Bubble({ m }: { m: MessageOut }) {
  const who = m.role === "user" ? "Visitor" : m.provider === "none" ? "Assistant (fallback)" : `Assistant${m.provider ? ` · ${m.provider}` : ""}`;
  return (
    <div className={`msg-wrap ${m.role}`}>
      <div className={`msg ${m.role}`}>{m.content}</div>
      <span className="msg-meta">
        {who} · {dateTime(m.created_at)}
      </span>
    </div>
  );
}
