"""Public knowledge base: approved service and FAQ content only.

Pages start as markdown in content/public/*.md. Staff can edit them, or add pages, from the
dashboard: each page is a versioned config key `content.public.<slug>`, and a published
version overrides the file. Publishing re-indexes straight away. Ingestion is idempotent:
unchanged pages are skipped (content hash), edited pages are re-chunked, and pages that no
longer exist are deleted from the index so withdrawn content can't be quoted.

Every page must pass the same rules as the assistant's replies (no prices, rates,
guarantees or delivery promises) before it can be saved or published.
"""

import argparse
import asyncio
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from assistant import guardrails
from assistant.kb.embeddings import Embedder, build_embedder
from assistant.models import ConfigStatus, ConfigVersion, Corpus, Document, PublicChunk, Sensitivity
from assistant.settings import get_settings

log = structlog.get_logger()

MAX_CHUNK_CHARS = 1200
CONTENT_PREFIX = "content.public."
SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,59}")
_FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


@dataclass
class ParsedDoc:
    source_uri: str
    title: str
    doc_type: str
    chunks: list[tuple[str | None, str]]  # (section, text)
    content_hash: str
    meta: dict[str, str] = field(default_factory=dict)  # front matter


@dataclass
class Hit:
    title: str
    section: str | None
    doc_type: str | None
    content: str
    score: float


def parse_markdown(source_uri: str, raw: str) -> ParsedDoc:
    meta: dict[str, str] = {}
    body = raw
    if m := _FRONT.match(raw):
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        body = raw[m.end() :]
    title = meta.get("title") or Path(source_uri).stem.replace("-", " ").title()

    sections: list[tuple[str | None, list[str]]] = [(None, [])]
    for line in body.splitlines():
        if line.startswith("## "):
            sections.append((line[3:].strip(), []))
        elif not line.startswith("# "):  # the H1 duplicates the title
            sections[-1][1].append(line)

    chunks: list[tuple[str | None, str]] = []
    for section, lines in sections:
        text = "\n".join(lines).strip()
        if not text:
            continue
        for piece in _split(text):
            header = f"{title} — {section}" if section else title
            chunks.append((section, f"{header}\n\n{piece}"))
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return ParsedDoc(source_uri, title, meta.get("type", "page"), chunks, digest, meta)


def _split(text: str) -> list[str]:
    if len(text) <= MAX_CHUNK_CHARS:
        return [text]
    out, cur = [], ""
    for para in text.split("\n\n"):
        if cur and len(cur) + len(para) + 2 > MAX_CHUNK_CHARS:
            out.append(cur)
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        out.append(cur)
    return out


def uri_for(slug: str) -> str:
    return f"content/public/{slug}.md"


def slug_for(key_or_uri: str) -> str:
    return Path(key_or_uri.removeprefix(CONTENT_PREFIX)).stem


def content_issues(raw: str) -> list[str]:
    """Why this page can't be published (empty list = fine)."""
    if not raw.strip():
        return ["The page is empty."]
    return [
        f'{v.rule.replace("_", " ")}: "{v.match}"'
        for v in guardrails.find_violations(raw)
        if v.rule and v.match
    ]


async def page_sources(session: AsyncSession, directory: Path) -> dict[str, tuple[str, str]]:
    """uri -> (markdown, origin). Published dashboard versions override the files."""

    def read() -> dict[str, str]:
        return {
            uri_for(f.stem): f.read_text(encoding="utf-8") for f in sorted(directory.glob("*.md"))
        }

    pages = {uri: (raw, "file") for uri, raw in (await asyncio.to_thread(read)).items()}
    rows = await session.execute(
        select(ConfigVersion.key, ConfigVersion.value).where(
            ConfigVersion.key.startswith(CONTENT_PREFIX),
            ConfigVersion.status == ConfigStatus.published,
        )
    )
    for key, value in rows.all():
        if isinstance(value, str) and SLUG.fullmatch(slug_for(key)):
            pages[uri_for(slug_for(key))] = (value, "dashboard")
    return pages


async def ingest_dir(
    session: AsyncSession, embedder: Embedder, directory: Path, *, commit: bool = True
) -> dict[str, int]:
    pages = await page_sources(session, directory)
    return await ingest_pages(session, embedder, {u: raw for u, (raw, _) in pages.items()}, commit)


async def ingest_pages(
    session: AsyncSession, embedder: Embedder, pages: dict[str, str], commit: bool = True
) -> dict[str, int]:
    stats = {"added": 0, "updated": 0, "unchanged": 0, "removed": 0, "chunks": 0}
    seen: set[str] = set()
    for uri, raw in sorted(pages.items()):
        seen.add(uri)
        doc = parse_markdown(uri, raw)
        existing = (
            await session.execute(
                select(Document).where(Document.corpus == Corpus.public, Document.source_uri == uri)
            )
        ).scalar_one_or_none()
        if existing and existing.content_hash == doc.content_hash:
            stats["unchanged"] += 1
            continue
        if existing:
            await session.execute(delete(PublicChunk).where(PublicChunk.document_id == existing.id))
            existing.title, existing.doc_type = doc.title, doc.doc_type
            existing.content_hash = doc.content_hash
            row = existing
            stats["updated"] += 1
        else:
            row = Document(
                corpus=Corpus.public,
                title=doc.title,
                doc_type=doc.doc_type,
                source_uri=uri,
                sensitivity=Sensitivity.public,
                allowed_roles=[],
                content_hash=doc.content_hash,
            )
            session.add(row)
            await session.flush()
            stats["added"] += 1
        vectors = await embedder.embed_documents([text for _, text in doc.chunks])
        for i, ((section, text), vec) in enumerate(zip(doc.chunks, vectors, strict=True)):
            session.add(
                PublicChunk(document_id=row.id, ord=i, section=section, content=text, embedding=vec)
            )
        stats["chunks"] += len(doc.chunks)

    gone = (
        (
            await session.execute(
                select(Document).where(
                    Document.corpus == Corpus.public, Document.source_uri.not_in(seen or {""})
                )
            )
        )
        .scalars()
        .all()
    )
    for d in gone:
        await session.delete(d)  # chunks cascade
        stats["removed"] += 1
    if commit:
        await session.commit()
    else:
        await session.flush()
    return stats


async def search(
    session: AsyncSession,
    embedder: Embedder,
    query: str,
    *,
    k: int | None = None,
    min_score: float | None = None,
) -> list[Hit]:
    s = get_settings()
    k = k or s.kb_top_k
    min_score = s.kb_min_score if min_score is None else min_score
    qv = await embedder.embed_query(query)
    dist = PublicChunk.embedding.cosine_distance(qv).label("dist")
    rows = (
        await session.execute(
            select(
                PublicChunk.content, PublicChunk.section, Document.title, Document.doc_type, dist
            )
            .join(Document, Document.id == PublicChunk.document_id)
            .where(Document.corpus == Corpus.public)
            .order_by(dist)
            .limit(k)
        )
    ).all()
    hits = [Hit(r.title, r.section, r.doc_type, r.content, round(1 - r.dist, 3)) for r in rows]
    return [h for h in hits if h.score >= min_score]


def main() -> None:
    """CLI: assistant-ingest [--dir content/public]"""
    from assistant.db import dispose_engine, get_sessionmaker
    from assistant.logs import configure_logging

    configure_logging(json=False)
    ap = argparse.ArgumentParser(description="Index approved public content")
    ap.add_argument("--dir", default=get_settings().kb_content_dir)
    args = ap.parse_args()

    async def run() -> None:
        async with get_sessionmaker()() as session:
            stats = await ingest_dir(session, build_embedder(), Path(args.dir))
        await dispose_engine()
        print(stats)

    asyncio.run(run())


if __name__ == "__main__":
    main()
