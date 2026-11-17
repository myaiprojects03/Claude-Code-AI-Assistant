"""Public knowledge base: approved service and FAQ content only.

Source of truth is markdown in content/public/*.md (reviewed like code). Ingestion is
idempotent: unchanged files are skipped (content hash), edited files are re-chunked, and
files that were removed are deleted from the index so withdrawn content can't be quoted.
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

from assistant.kb.embeddings import Embedder, build_embedder
from assistant.models import Corpus, Document, PublicChunk, Sensitivity
from assistant.settings import get_settings

log = structlog.get_logger()

MAX_CHUNK_CHARS = 1200
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


async def ingest_dir(session: AsyncSession, embedder: Embedder, directory: Path) -> dict[str, int]:
    stats = {"added": 0, "updated": 0, "unchanged": 0, "removed": 0, "chunks": 0}
    files = await asyncio.to_thread(lambda: sorted(directory.glob("*.md")))
    seen: set[str] = set()
    for f in files:
        uri = f"content/public/{f.name}"
        seen.add(uri)
        doc = parse_markdown(uri, await asyncio.to_thread(f.read_text, encoding="utf-8"))
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
    await session.commit()
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
