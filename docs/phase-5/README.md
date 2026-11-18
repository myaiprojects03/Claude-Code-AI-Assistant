# Phase 5 · Internal knowledge assistant

A private assistant for consultants. It answers from internal documents (proposals, technical
docs, templates, case studies, SOPs) with source references and helps draft proposals,
meeting summaries and follow-up emails. **Client data stays strictly separated, and the
documents never leave this machine**: answers come from a local model (Ollama).

## What's built

| Piece | Where | Notes |
|---|---|---|
| Local model | Ollama, `qwen2.5:3b` by default | Installed to `D:\Ollama`, models in `D:\Ollama\models` (`OLLAMA_MODELS`). `INTERNAL_LLM_CHAIN=ollama` |
| Documents | `content/internal/*.md` | 17 fictional documents: 8 firm-wide, plus 3 clients (Northbridge, Halcyon, Brightwater). Front matter: `client`, `sensitivity`, optional `roles` |
| Indexing | `kb/internal.py`, `assistant-ingest-internal` | Local fastembed embeddings in a separate table from public content. Idempotent; re-indexes when the text **or** its access metadata changes. Client documents must be `client_confidential` and name a known client. |
| Access model | `client_access` table, `access_for()` | Admins and managers: every client. Consultants: firm-wide documents plus their assigned clients. A document can restrict roles (e.g. commercial terms: admin, manager). |
| Permission-filtered search | `search()` | The access filter is part of the SQL query that ranks by similarity, so documents a user can't see are never retrieved and never reach the model. pgvector iterative HNSW scan keeps result counts right under filtering. |
| Answers with sources | `retrieve_for_turn()`, `POST /v1/internal/conversations/{id}/messages` | Each turn retrieves the top 5 allowed sections, labelled as firm-wide or a named client. The response includes `sources` (`n`, title, section, client, uri, score). The source list is stored with the conversation; retrievals are audited. |
| Drafting | prompt + templates in the corpus | Drafts follow the matching template, use only known facts, and mark gaps as `[TO CONFIRM]` |
| Fact guard | `grounding.py`, used by the orchestrator | Every internal reply is checked: a key fact (money, percentage, time, date, IP range, code, quantity) that is in neither the sources the model was given nor the person's own messages is replaced with `[TO CONFIRM]` before the reply is shown |
| Login | Keycloak (staff roles), existing internal chat API | Each internal conversation is private to the staff member who started it |

## Live records (added in Phase 6)

Documents are written knowledge. The assistant can also answer **status questions about live
records**, such as "what's the status of Alex Bob's query?" or "any update on H-524?" (`kb/records.py`).

- **What it looks up:** a person named in full or by email, a client company named in a status-type question ("anything
  open for Halcyon?"), or a reference (H-524, T-1003). Only what the question names is looked
  up, by plain SQL, never by similarity, so records can't drift into unrelated answers. A first name on
  its own isn't enough.
- **What it returns:** a "current status" headline, the handovers (who has them), discovery calls,
  open tickets and, for managers and admins only, the lead's answers. Never transcripts; those stay
  on the dashboard, where opening one is audited.
- **Client separation:** anything tied to a client company follows the same client access as that
  client's documents. A consultant not assigned to Halcyon gets no Halcyon records, and no hint that
  any exist. Visitors with no client company are visible to all staff, as on the dashboard.
- **In the answer:** records are numbered after the documents, shown as "Live record" with a link to
  the dashboard page, audited with the document ids, and checked by the fact guard like any source.
  For person and reference questions, only documents that are a strong match (score >= 0.68) are kept.
- **Checked on the real local model:** asked about Alex Bob, both roles got the P1 security handover,
  who had taken it, and the booked call; only the admin got the lead answers.

## Safeguards added after the evals

These weren't caught by the unit tests. Running the real local model found them.

| Finding | Fix |
|---|---|
| Asked to *write* a meeting summary, the 3B model called `send_email` to an invented address (`acme-it-consulting-team@domain.com`) | `send_email` only acts when the user's own latest message asks to send, and only to an address the user typed. `notify_team` needs an explicit "post/notify/tell the team". |
| Both models presented the firm-wide Azure standard as "what Halcyon uses" (no leak, but made up) | Sources are labelled firm-wide or client. If the question names a client and no source is about them, the model is told to say it can't find it. The wording is the same whether the user lacks access or no document exists, so it reveals nothing. |
| Asked to draft a proposal, both models tried `send_email` instead of writing it; the refused call left an empty reply | Send and post tools are only *offered* to the model when the person's message asks to send or post (the in-tool checks stay as a second line) |
| Proposal drafts invented prices, payment milestones and durations (£50,000 per phase, 50% on kickoff, "weeks 6–36") even when told not to | A stricter drafting rule (any figure not in the sources must be `[TO CONFIRM]`), plus the runtime fact guard above, which catches what the prompt doesn't |
| The 3B and 7B sometimes returned empty replies | One retry without tools; if still empty, a clear "couldn't produce an answer", flagged as degraded |
| The 3B once looped until the 10-minute timeout | Local output capped at 2,000 tokens (`OLLAMA_MAX_TOKENS`); a reply cut at the cap says so |
| A 7B proposal took over 5 minutes and hit the old timeout | `OLLAMA_TIMEOUT_S` raised to 600 |
| The eval runner's DB connections dropped during long CPU inference | `pool_pre_ping` in the runner (the app already had it) |

## Results (2026-11-17)

**Automated tests:** 193 passing at the end of Phase 5 (219 now), including the leak tests in `tests/test_db_phase5.py`:
- A Northbridge-only consultant can never retrieve Halcyon or Brightwater text, across 5
  adversarial queries ("ignore your permissions…", "compare all clients"…).
- An unassigned consultant sees only firm-wide documents.
- A consultant assigned to Halcyon still can't see the managers-only commercial terms.
- The text the model receives never contains another client's facts.
- The public website assistant can never reach internal documents.
- The citation checker itself is tested (`tests/test_grounding.py`): it catches invented
  numbers, wrong citations and dangling `[n]` markers, and doesn't flag paraphrases
  ("6pm" for "18:00", "£38,400" for "£38400").

**Evals on the local model** (`python -m evals.run_evals --internal`), 10 cases:

| Model | Score | Time per answer | Facts the guard replaced |
|---|---|---|---|
| qwen2.5:3b (default) | **10/10** | 11–200 s | 3 (invented payment percentages in one proposal) |
| qwen2.5:7b | **10/10** | 23–650 s | 1 (an invented "36 weeks" in one proposal) |

Two checks run on every case:
- **Citation accuracy:** every key fact in the answer must be in the source it cites, or, if
  uncited, in at least one source the model was given (or in the user's question). Every
  `[n]` must point to a real source. A real document cited for a made-up figure fails.
- **Proposal drafting:** `proposal-single` (a Northbridge-only consultant) and `proposal-cross`
  (a manager who can see every client, so isolation can't rely on permissions) must follow the
  proposal template (at least 5 of its sections) and contain no fact from another client. Each
  client's distinctive facts are canary strings checked in the reply.

The eval report lists what the fact guard replaced, so the guard can't hide how often the raw
model makes figures up. Both models still invent figures in proposals now and then; the guard
is what keeps them out of the draft.

Cases: own-client facts, firm SOP, forbidden client, prompt injection, managers-only figure,
unknown fact, follow-up email draft, meeting summary from template, single-client proposal,
proposal by a manager with access to every client.

## Run it

```bash
D:\Ollama\ollama.exe serve                 # if Ollama isn't already running
cd apps/api
../../.venv/Scripts/assistant-seed         # clients + consultant.dev assigned to Northbridge
../../.venv/Scripts/assistant-ingest-internal
../../.venv/Scripts/python ../../scripts/chat_cli.py --staff consultant.dev --password <dev password>
```

To give a consultant access to a client, add a row to `client_access` (staff email and
company). An admin UI for this belongs in the dashboard (Phase 6).

## Known limits

- Answers take 10–60 s on this CPU; full proposals take 1–11 minutes. A GPU, or a cloud model for non-confidential questions,
  would be much faster.
- The small model rarely puts `[1]`-style markers inline; the source list is always returned
  with each answer, and the fact guard checks uncited figures against all the sources.
- The fact guard checks figures and identifiers, not wording. An invented claim with no
  number in it ("they use Intune") is caught only by the prompt rules and the evals.
- There's no internal-assistant page in the dashboard yet (Phase 6). For now, use the API or
  the CLI.
