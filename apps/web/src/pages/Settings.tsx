import { useEffect, useState } from "react";
import { useAuth } from "../lib/auth";
import { useConfigCatalogue, useConfigVersions, usePublish, useSaveDraft, type ConfigEntry } from "../lib/api";
import { dateTime } from "../lib/format";
import { Badge, Empty, ErrorState, Loading, PageHead, Panel } from "../components/ui";
import { FORM_KEYS, RuleEditor } from "../components/RuleEditors";

export default function Settings() {
  const q = useConfigCatalogue();
  const [open, setOpen] = useState<string | null>(null);
  const { hasRole } = useAuth();
  const canEdit = hasRole("admin");

  return (
    <>
      <PageHead
        title="Assistant settings"
        lede={
          <>
            Change what the assistant says and how it decides, <b>without a developer</b>. Every change is a
            versioned draft; publishing takes effect within 10 seconds and is recorded in the audit log.
            {!canEdit && " You can view settings; publishing needs the admin role."}
          </>
        }
      />
      <Panel>
        {q.isLoading ? (
          <Loading rows={8} />
        ) : q.isError ? (
          <ErrorState error={q.error} />
        ) : !q.data?.length ? (
          <Empty>No editable settings.</Empty>
        ) : (
          <div className="accordion">
            {q.data.map((entry) => (
              <SettingItem
                key={entry.key}
                entry={entry}
                open={open === entry.key}
                toggle={() => setOpen(open === entry.key ? null : entry.key)}
                canEdit={canEdit}
              />
            ))}
          </div>
        )}
      </Panel>
    </>
  );
}

function asText(v: unknown) {
  return typeof v === "string" ? v : JSON.stringify(v, null, 2);
}

function SettingItem({ entry, open, toggle, canEdit }: { entry: ConfigEntry; open: boolean; toggle: () => void; canEdit: boolean }) {
  const isText = typeof entry.value === "string";
  const isForm = FORM_KEYS.has(entry.key) && typeof entry.value === "object" && entry.value !== null;
  const [text, setText] = useState(asText(entry.value));
  const [asJson, setAsJson] = useState(false);
  // Form mode keeps an object; the JSON box and the form share it through `text`.
  const formValue = (() => {
    try {
      return JSON.parse(text) as Record<string, unknown>;
    } catch {
      return null;
    }
  })();
  const [error, setError] = useState<string | null>(null);
  const versions = useConfigVersions(entry.key, open);
  const save = useSaveDraft(entry.key);
  const publish = usePublish(entry.key);

  useEffect(() => {
    if (!open) setText(asText(entry.value));
  }, [entry.value, open]);

  const parsed = (): { ok: true; value: unknown } | { ok: false; message: string } => {
    if (isText) return text.trim() ? { ok: true, value: text.trim() } : { ok: false, message: "Can't be empty." };
    try {
      return { ok: true, value: JSON.parse(text) };
    } catch (e) {
      return { ok: false, message: `Not valid JSON: ${(e as Error).message}` };
    }
  };
  const dirty = text !== asText(entry.value);
  const latestDraft = versions.data?.find((v) => v.status === "draft");

  const onSave = () => {
    const p = parsed();
    if (!p.ok) return setError(p.message);
    setError(null);
    save.mutate(p.value);
  };

  return (
    <div className={`acc-item${open ? " open" : ""}`}>
      <button className="acc-head" onClick={toggle} aria-expanded={open}>
        <span>
          <span className="strong">{entry.description}</span>
          <span className="micro faint" style={{ display: "block", marginTop: 3 }}>{entry.key}</span>
        </span>
        <span className="small muted">
          {entry.source === "published" ? `v${entry.published_version}` : "Default"}
          {entry.draft_versions ? ` · ${entry.draft_versions} draft` : ""}
        </span>
        <span className="plus" aria-hidden>+</span>
      </button>
      {open && (
        <div className="acc-body">
          <label className="sr-only" htmlFor={`cfg-${entry.key}`}>{entry.description}</label>
          {isForm && !asJson && formValue ? (
            <RuleEditor
              ruleKey={entry.key}
              value={formValue}
              readOnly={!canEdit}
              onChange={(v) => setText(JSON.stringify(v, null, 2))}
            />
          ) : isText ? (
            <textarea
              id={`cfg-${entry.key}`}
              className="input"
              style={{ minHeight: 110, fontFamily: "var(--font)", fontSize: 14 }}
              value={text}
              readOnly={!canEdit}
              onChange={(e) => setText(e.target.value)}
            />
          ) : (
            <textarea
              id={`cfg-${entry.key}`}
              className="input"
              spellCheck={false}
              value={text}
              readOnly={!canEdit}
              onChange={(e) => setText(e.target.value)}
            />
          )}
          {isText && (
            <div>
              <span className="micro faint">Preview, as a visitor sees it</span>
              <div className="msg assistant" style={{ marginTop: 6 }}>{text || "…"}</div>
            </div>
          )}
          {isForm && canEdit && (
            <button className="btn ghost" style={{ justifySelf: "start" }} onClick={() => setAsJson(!asJson)} disabled={!formValue && asJson}>
              {asJson ? "Back to the form" : "Advanced: edit as JSON"}
            </button>
          )}
          {error && <div className="error-box" role="alert">{error}</div>}
          {(save.isError || publish.isError) && <ErrorState error={save.error ?? publish.error} />}
          {canEdit && (
            <div className="toolbar">
              <button className="btn" disabled={!dirty || save.isPending} onClick={onSave}>
                <span className="glyph">↪</span> {save.isPending ? "Saving…" : "Save as draft"}
              </button>
              {latestDraft && (
                <button
                  className="btn primary"
                  disabled={publish.isPending || dirty}
                  title={dirty ? "Save your changes as a draft first" : undefined}
                  onClick={() => publish.mutate(latestDraft.version, { onSuccess: () => save.reset() })}
                >
                  <span className="glyph">↪</span> Publish v{latestDraft.version}
                </button>
              )}
              {save.isSuccess && !latestDraft && <span className="small muted">Draft saved.</span>}
            </div>
          )}
          {versions.data && versions.data.length > 0 && (
            <div className="rows">
              {versions.data.slice(0, 6).map((v) => (
                <div className="row" key={v.version}>
                  <div>
                    <span className="strong num">v{v.version}</span>{" "}
                    <span className="small muted">
                      {v.status === "published" ? `published ${v.published_at ? dateTime(v.published_at) : ""}` : `created ${dateTime(v.created_at)}`}
                    </span>
                  </div>
                  <Badge value={v.status} kind="outline" />
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
