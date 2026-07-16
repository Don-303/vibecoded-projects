#!/usr/bin/env python
"""One-time (re-runnable) setup step: clone reference lecture corpora and
build a local embedding index for retrieval-augmented tutoring.

Needs internet the first time (git clone + embedding model pull via
app/rag.py's EMBED_MODEL). After that, everything the tutor does with this
index is fully offline.

Usage:
    .venv/bin/python scripts/build_rag_index.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(PROJECT_ROOT))
from app import rag  # noqa: E402

CORPUS_DIR = rag.CORPUS_DIR
SOURCES = rag.SOURCES  # shared registry -- also used by app/rag.py at runtime

MAX_CHUNK_CHARS = 1200
MAX_CELL_CHARS = 4000
EMBED_BATCH_SIZE = 32

FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
RAW_HTML_BLOCK_RE = re.compile(r"```\{raw\}\s*html.*?```", re.DOTALL)


def clone_or_update(name: str, url: str) -> Path:
    dest = CORPUS_DIR / name
    if dest.exists():
        print(f"[{name}] updating...")
        subprocess.run(["git", "-C", str(dest), "pull", "--ff-only"], check=True)
    else:
        print(f"[{name}] cloning...")
        CORPUS_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--depth", "1", url, str(dest)], check=True)
    return dest


def _accumulate(pieces: list[str]) -> list[str]:
    chunks: list[str] = []
    buffer = ""
    for text in pieces:
        text = text.strip()
        if not text:
            continue
        if len(buffer) + len(text) + 2 > MAX_CHUNK_CHARS and buffer:
            chunks.append(buffer)
            buffer = text
        else:
            buffer = f"{buffer}\n\n{text}" if buffer else text
    if buffer:
        chunks.append(buffer)
    return chunks


def cell_text(cell: dict) -> str:
    source = cell.get("source", "")
    text = "".join(source) if isinstance(source, list) else source
    text = text.strip()
    if not text:
        return ""
    if cell.get("cell_type") == "code":
        return f"```python\n{text[:MAX_CELL_CHARS]}\n```"
    return text[:MAX_CELL_CHARS]


def chunk_notebook(path: Path) -> list[str]:
    try:
        nb = json.loads(path.read_text(errors="ignore"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []
    return _accumulate([cell_text(cell) for cell in nb.get("cells", [])])


def chunk_myst_markdown(path: Path) -> list[str]:
    text = path.read_text(errors="ignore")
    text = FRONTMATTER_RE.sub("", text)
    text = RAW_HTML_BLOCK_RE.sub("", text)
    paragraphs = re.split(r"\n\s*\n", text)
    return _accumulate([p[:MAX_CELL_CHARS] for p in paragraphs])


def title_from_path(path: Path) -> str:
    return path.stem.replace("-", " ").replace("_", " ")


def main() -> None:
    all_chunks: list[dict] = []

    for name, url, subdir, pattern in SOURCES:
        repo_dir = clone_or_update(name, url)
        search_dir = repo_dir / subdir if (repo_dir / subdir).exists() else repo_dir
        files = sorted(search_dir.rglob(pattern))
        print(f"[{name}] found {len(files)} files")
        chunk_fn = chunk_notebook if pattern == "*.ipynb" else chunk_myst_markdown
        for file_path in files:
            title = title_from_path(file_path)
            for chunk in chunk_fn(file_path):
                all_chunks.append(
                    {"text": chunk, "source_repo": name,
                     "source_file": file_path.name, "title": title}
                )

    print(f"Total chunks: {len(all_chunks)}")
    if not all_chunks:
        print("No chunks produced, aborting.")
        return

    print(f"Embedding with {rag.EMBED_MODEL} (batches of {EMBED_BATCH_SIZE})...")
    embeddings = []
    texts = [c["text"] for c in all_chunks]
    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[i : i + EMBED_BATCH_SIZE]
        resp = rag.embed_batch(batch)
        embeddings.extend(resp)
        print(f"  {min(i + EMBED_BATCH_SIZE, len(texts))}/{len(texts)}", end="\r")
    print()

    rag.save_index(embeddings, all_chunks)
    print(f"Saved index to {rag.INDEX_DIR}")


if __name__ == "__main__":
    main()
