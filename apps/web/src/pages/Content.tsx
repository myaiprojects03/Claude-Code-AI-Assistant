import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../lib/auth";
import {
  askContent,
  previewContent,
  useConfigVersions,
  useContent,
  usePublish,
  useSaveDraft,
  useUnanswered,
  type AskHit,
  type ContentPage,
  type ContentPreview,
} from "../lib/api";
import { dateTime, relative } from "../lib/format";
import { Badge, Empty, ErrorState, Loading, PageHead, Panel } from "../components/ui";

const ORIGIN: Record<ContentPage["origin"], string> = {
  file: "From the repository",
  dashboard: "Edited here",
  draft: "New, not published",
};

export default function Content() {
  const pages = useContent();
  const [open, setOpen] = useState<string | null>(null);
  const [creating, setCreating] = useState<ContentPage | null>(null);
  const { hasRole } = useAuth();
  const canEdit = hasRole("admin");

  const list = [...(pages.data ?? []), ...(creating && !pages.data?.some((p) => p.slug === creating.slug) ? [creating] : [])];

  return (
    <>
      <PageHead
        title="Website content"
        lede={
          <>
            The FAQ and service pages are <b>the only source</b> the website assistant answers from. Edit a page, check it,
            and publish: the assistant uses the new text <b>immediately</b>. Prices, rates, guarantees and delivery promises
            are blocked, the same as in the assistant's replies.
            {!canEdit && " You can view and test content; saving and publishing need the admin role."}
          </>
        }
        actions={canEdit && <NewPage existing={pages.data ?? []} onCreate={(p) => { setCreating(p); setOpen(p.slug); }} />}
      />

      <Panel>
        {pages.isLoading ? (
          <Loading rows={5} />
        ) : pages.isError ? (
          <ErrorState error={pages.error} />
        ) : !list.length ? (
          <Empty>No content pages.</Empty>
        ) : (
          <div className="accordion">
            {list.map((p) => (
              <PageItem
                key={p.slug}
                page={p}
                open={open === p.slug}
                toggle={() => setOpen(open === p.slug ? null : p.slug)}
                canEdit={canEdit}
              />
            ))}
          </div>
        )}
      </Panel>

      <div className="grid two">
        <TryQuestion />
        <Unanswered />
      </div>
    </>
  );
}

function NewPage({ existing, onCreate }: { existing: ContentPage[]; onCreate: (p: ContentPage) => void }) {
  const [title, setTitle] = useState("");
  const slug = title.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60);
  const taken = existing.some((p) => p.slug === slug);
  return (
    <form
      className="toolbar"
      onSubmit={(e) => {
        e.preventDefault();
        if (!slug || taken) return;
        onCreate({
          slug,
          key: `content.public.${slug}`,
          title,
          doc_type: "page",
          raw: `---\ntitle: ${title.trim()}\ntype: page\n---\n# ${title.trim()}\n\n## First section\nWrite the approved answer here.\n`,
          origin: "draft",
          published_version: null,
          published_at: null,
          draft_versions: 0,
          sections: 0,
          indexed_chunks: 0,
        });
        setTitle("");
      }}
    >
      <label className="sr-only" htmlFor="new-page">New page title</label>
      <input id="new-page" className="input" placeholder="New page title" value={title} onChange={(e) => setTitle(e.target.value)} />
      <button className="btn" disabled={!slug || taken}>
        <span className="glyph">↪</span> {taken ? "Name in use" : "Add page"}
      </button>
    </form>
  );
}

function usePreview(raw: string, enabled: boolean) {
  const [preview, setPreview] = useState<ContentPreview | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    const t = setTimeout(() => {
      previewContent(raw).then((p) => live && setPreview(p)).catch(() => live && setPreview(null));
    }, 350);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [raw, enabled]);
  return preview;
}

function PageItem({ page, open, toggle, canEdit }: { page: ContentPage; open: boolean; toggle: () => void; canEdit: boolean }) {
  const [text, setText] = useState(page.raw);
  const versions = useConfigVersions(page.key, open);
  const save = useSaveDraft(page.key);
  const publish = usePublish(page.key);
  const latestDraft = versions.data?.find((v) => v.status === "draft");
  const preview = usePreview(text, open);

  useEffect(() => {
    if (!open) setText(page.raw);
  }, [page.raw, open]);
  // A new page (or one with only drafts) starts from its newest draft.
  useEffect(() => {
    if (open && !page.raw && latestDraft && typeof latestDraft.value === "string") setText(latestDraft.value);
  }, [open, page.raw, latestDraft]);

  const issues = preview?.issues ?? [];
  // Unsaved = differs from the newest draft if there is one, otherwise from the live text.
  const saved = typeof latestDraft?.value === "string" ? latestDraft.value : page.raw;
  const dirty = text !== saved;

  return (
    <div className={`acc-item${open ? " open" : ""}`}>
      <button className="acc-head" onClick={toggle} aria-expanded={open}>
        <span>
          <span className="strong">{page.title}</span>
          <span className="micro faint" style={{ display: "block", marginTop: 3 }}>
            {ORIGIN[page.origin]} · {page.sections} sections · {page.indexed_chunks} indexed
          </span>
        </span>
        <span className="small muted">
          {page.published_version ? `v${page.published_version}` : page.origin === "file" ? "File" : "Draft"}
          {page.draft_versions ? ` · ${page.draft_versions} draft` : ""}
        </span>
        <span className="plus" aria-hidden>+</span>
      </button>
      {open && (
        <div className="acc-body">
          <div className="grid two">
            <div style={{ display: "grid", gap: 8, alignContent: "start" }}>
              <label className="micro faint" htmlFor={`page-${page.slug}`}>
                Markdown · each <span className="num">##</span> heading becomes a section the assistant can quote
              </label>
              <textarea
                id={`page-${page.slug}`}
                className="input"
                style={{ minHeight: 340 }}
                spellCheck
                value={text}
                readOnly={!canEdit}
                onChange={(e) => setText(e.target.value)}
              />
            </div>
            <div style={{ display: "grid", gap: 10, alignContent: "start" }}>
              <span className="micro faint">Check</span>
              {!preview ? (
                <Loading rows={3} />
              ) : issues.length ? (
                <div className="error-box" role="alert">
                  Can't be published yet:
                  <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                    {issues.map((i) => <li key={i}>{i}</li>)}
                  </ul>
                </div>
              ) : (
                <div className="small"><span className="dot" aria-hidden />No prices, rates or promises found.</div>
              )}
              {preview && (
                <div className="rows">
                  {preview.sections.map((s, i) => (
                    <div className="row" key={`${s.section}-${i}`}>
                      <div className="title small">{s.section}</div>
                      <span className="micro faint num">{s.chars} chars</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
          {(save.isError || publish.isError) && <ErrorState error={save.error ?? publish.error} />}
          {canEdit && (
            <div className="toolbar">
              <button className="btn" disabled={!dirty || issues.length > 0 || save.isPending} onClick={() => save.mutate(text)}>
                <span className="glyph">↪</span> {save.isPending ? "Saving…" : "Save as draft"}
              </button>
              {latestDraft && (
                <button
                  className="btn primary"
                  disabled={publish.isPending || dirty}
                  title={dirty ? "Save your changes as a draft first" : undefined}
                  onClick={() => publish.mutate(latestDraft.version, { onSuccess: () => save.reset() })}
                >
                  <span className="glyph">↪</span> {publish.isPending ? "Publishing…" : `Publish v${latestDraft.version}`}
                </button>
              )}
              {publish.isSuccess && !latestDraft && <span className="small muted">Published. The assistant is using it now.</span>}
            </div>
          )}
          {versions.data && versions.data.length > 0 && (
            <div className="rows">
              {versions.data.slice(0, 8).map((v) => (
                <div className="row" key={v.version}>
                  <div>
                    <span className="strong num">v{v.version}</span>{" "}
                    <span className="small muted">
                      {v.created_by ? `by ${v.created_by} · ` : ""}
                      {v.status === "published" && v.published_at ? `published ${dateTime(v.published_at)}` : `created ${dateTime(v.created_at)}`}
                    </span>
                  </div>
                  <div className="toolbar">
                    {canEdit && v.status === "archived" && (
                      <button className="btn ghost" disabled={publish.isPending} onClick={() => publish.mutate(v.version)}>
                        Restore
                      </button>
                    )}
                    <Badge value={v.status} kind="outline" />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function TryQuestion() {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<AskHit[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  return (
    <Panel title="Test a question" meta="What the website assistant finds in the live content">
      <form
        className="toolbar"
        onSubmit={async (e) => {
          e.preventDefault();
          if (q.trim().length < 2) return;
          setBusy(true);
          setError(null);
          try {
            setHits(await askContent(q.trim()));
          } catch (err) {
            setError(err);
          } finally {
            setBusy(false);
          }
        }}
      >
        <label className="sr-only" htmlFor="try-q">Question</label>
        <input id="try-q" className="input" style={{ flex: 1 }} placeholder="e.g. Do you work with schools?" value={q} onChange={(e) => setQ(e.target.value)} />
        <button className="btn" disabled={busy}><span className="glyph">↪</span> {busy ? "Searching…" : "Search"}</button>
      </form>
      {error ? (
        <ErrorState error={error} />
      ) : hits === null ? null : !hits.length ? (
        <Empty>Nothing approved covers this. The assistant would say it doesn't know and offer a call.</Empty>
      ) : (
        <div className="rows" style={{ marginTop: 8 }}>
          {hits.map((h, i) => (
            <div className="row" key={i}>
              <div>
                <div className="title small">{h.title}{h.section ? ` · ${h.section}` : ""}</div>
                <div className="meta" style={{ whiteSpace: "pre-wrap" }}>{h.text.split("\n\n").slice(1).join("\n\n").slice(0, 220)}</div>
              </div>
              <span className="micro faint num">{Math.round(h.score * 100)}% match</span>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function Unanswered() {
  const q = useUnanswered();
  return (
    <Panel title="Questions it couldn't answer" meta="Last 30 days, similar questions grouped · where content is missing">
      {q.isLoading ? (
        <Loading />
      ) : q.isError ? (
        <ErrorState error={q.error} />
      ) : !q.data?.length ? (
        <Empty>Every question in the last 30 days was covered by the content.</Empty>
      ) : (
        <div className="rows">
          {q.data.slice(0, 8).map((t) => (
            <div className="row" key={t.topic}>
              <div>
                <div className="title small">{t.topic}</div>
                <div className="meta">
                  {t.examples.length > 1 && <>Also: {t.examples.filter((e) => e !== t.topic).slice(0, 2).join(" · ")} · </>}
                  last asked {relative(t.last_asked)} ·{" "}
                  <Link to={`/conversations/${t.conversation_ids[0]}`}>open conversation</Link>
                </div>
              </div>
              <span className="badge strong num" aria-label={`${t.count} times`}>{t.count}×</span>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}
