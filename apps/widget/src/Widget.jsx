import { useEffect, useRef, useState } from "preact/hooks";

const STORE_KEY = "assistant-chat:v1";

// sessionStorage can throw (private mode, blocked storage): the widget must work without it.
function load() {
  try {
    return JSON.parse(sessionStorage.getItem(STORE_KEY)) || {};
  } catch {
    return {};
  }
}
function save(state) {
  try {
    sessionStorage.setItem(STORE_KEY, JSON.stringify(state));
  } catch {
    /* ignore */
  }
}

class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function post(url, body, token) {
  const res = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new ApiError(res.status, `HTTP ${res.status}`);
  return res.json();
}

export function Widget({ config }) {
  const saved = load();
  const [open, setOpen] = useState(false);
  const [session, setSession] = useState(saved.session || null);
  const [messages, setMessages] = useState(saved.messages || []);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const logRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => save({ session, messages }), [session, messages]);
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [messages, busy]);
  useEffect(() => {
    if (open && session) inputRef.current?.focus();
  }, [open, session]);

  async function startSession() {
    const s = await post(`${config.api}/v1/chat/sessions`, { consent: true });
    const next = { token: s.token, id: s.conversation_id };
    setSession(next);
    return { next, greeting: s.greeting };
  }

  async function accept() {
    setBusy(true);
    setError("");
    try {
      const { greeting } = await startSession();
      setMessages([{ role: "assistant", text: greeting }]);
    } catch {
      setError("We couldn't start the chat. Please try again in a moment.");
    } finally {
      setBusy(false);
    }
  }

  async function send(e) {
    e?.preventDefault();
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setError("");
    setMessages((m) => [...m, { role: "user", text }]);
    setBusy(true);
    try {
      let token = session?.token;
      let reply;
      try {
        reply = await post(`${config.api}/v1/chat/messages`, { text }, token);
      } catch (err) {
        if (err.status !== 401) throw err;
        ({ next: { token } } = await startSession()); // session expired: start a new one
        reply = await post(`${config.api}/v1/chat/messages`, { text }, token);
      }
      setMessages((m) => [...m, { role: "assistant", text: reply.reply }]);
    } catch (err) {
      setError(
        err.status === 429
          ? "You're sending messages quickly. Please wait a moment and try again."
          : "Sorry, your message didn't go through. Please try again."
      );
      setInput(text);
      setMessages((m) => m.slice(0, -1));
    } finally {
      setBusy(false);
    }
  }

  function onKey(e) {
    if (e.key === "Enter" && !e.shiftKey) send(e);
  }

  function reset() {
    setSession(null);
    setMessages([]);
    setError("");
  }

  return (
    <div class="ac" onKeyDown={(e) => e.key === "Escape" && setOpen(false)}>
      {open && (
        <section class="panel" role="dialog" aria-label={`Chat with ${config.title}`}>
          <header>
            <div>
              <strong>{config.title}</strong>
              <span class="sub">AI assistant · replies instantly</span>
            </div>
            <div class="head-actions">
              {session && (
                <button class="ghost" onClick={reset} title="Start a new conversation">
                  New chat
                </button>
              )}
              <button class="ghost icon" onClick={() => setOpen(false)} aria-label="Close chat">
                ×
              </button>
            </div>
          </header>

          {!session ? (
            <div class="consent">
              <p>
                Hi! I'm an AI assistant. I can explain our services, answer common questions and
                book a free call with a consultant.
              </p>
              <p class="small">
                We store this conversation so our team can follow up. See our{" "}
                <a href={config.privacyUrl} target="_blank" rel="noopener">
                  privacy notice
                </a>
                .
              </p>
              <button class="primary" onClick={accept} disabled={busy}>
                {busy ? "Starting…" : "Accept and start chatting"}
              </button>
              {error && <p class="error" role="alert">{error}</p>}
            </div>
          ) : (
            <>
              <div class="log" ref={logRef} role="log" aria-live="polite">
                {messages.map((m, i) => (
                  <div key={i} class={`msg ${m.role}`}>
                    {m.text}
                  </div>
                ))}
                {busy && (
                  <div class="msg assistant typing" aria-label="Assistant is typing">
                    <span />
                    <span />
                    <span />
                  </div>
                )}
              </div>
              {error && <p class="error" role="alert">{error}</p>}
              <form class="composer" onSubmit={send}>
                <textarea
                  ref={inputRef}
                  rows={1}
                  maxLength={4000}
                  placeholder="Type your message…"
                  value={input}
                  onInput={(e) => setInput(e.currentTarget.value)}
                  onKeyDown={onKey}
                  aria-label="Message"
                />
                <button class="primary" type="submit" disabled={busy || !input.trim()}>
                  Send
                </button>
              </form>
            </>
          )}
        </section>
      )}
      <button
        class="launcher"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-label={open ? "Close chat" : "Chat with us"}
      >
        {open ? "×" : "Chat with us"}
      </button>
    </div>
  );
}
