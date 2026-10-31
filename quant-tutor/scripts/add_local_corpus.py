#!/usr/bin/env python
"""Add a local (non-git) notebook collection to the corpus and embedding
index incrementally -- existing chunks are kept, so this takes minutes, not
the hours a full rebuild does.

Currently registers the MSc CF969 (Machine Learning in Finance) lab and
case-study notebooks from the local Google Drive mirror. The copies land in
corpus/<name>/ (gitignored, personal course material stays local-only).

Re-running replaces that source's chunks in the index rather than
duplicating them.

Usage:
    .venv/bin/python scripts/add_local_corpus.py
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app import rag  # noqa: E402
from scripts.build_rag_index import EMBED_BATCH_SIZE, chunk_notebook, title_from_path  # noqa: E402

# (corpus name, list of directories to copy *.ipynb from)
MSC_DIR = Path.home() / "GoogleDrive/MSc Financial Engineering/Machine learning in finance"
LOCAL_SOURCES = [
    ("msc-cf969", [MSC_DIR / "Labs/Notebooks", MSC_DIR / "Case Studies"]),
]


def copy_notebooks(name: str, source_dirs: list[Path]) -> Path:
    dest = rag.CORPUS_DIR / name
    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    for src_dir in source_dirs:
        if not src_dir.exists():
            print(f"[{name}] WARNING: {src_dir} not found, skipping")
            continue
        for nb in sorted(src_dir.glob("*.ipynb")):
            shutil.copy2(nb, dest / nb.name)
            copied += 1
    print(f"[{name}] copied {copied} notebooks to {dest}")
    return dest


def main() -> None:
    if not rag.EMBEDDINGS_PATH.exists():
        print(f"No existing index at {rag.INDEX_DIR}; run build_rag_index.py first.")
        sys.exit(1)

    embeddings = np.load(rag.EMBEDDINGS_PATH)
    chunks: list[dict] = json.loads(rag.CHUNKS_PATH.read_text())
    assert len(embeddings) == len(chunks)

    for name, source_dirs in LOCAL_SOURCES:
        dest = copy_notebooks(name, source_dirs)

        # Replace any previous chunks for this source (re-run safety).
        keep = [i for i, c in enumerate(chunks) if c["source_repo"] != name]
        if len(keep) != len(chunks):
            print(f"[{name}] replacing {len(chunks) - len(keep)} existing chunks")
            embeddings = embeddings[keep]
            chunks = [chunks[i] for i in keep]

        new_chunks = []
        for nb_path in sorted(dest.glob("*.ipynb")):
            title = title_from_path(nb_path)
            for chunk in chunk_notebook(nb_path):
                new_chunks.append(
                    {"text": chunk, "source_repo": name,
                     "source_file": nb_path.name, "title": title}
                )
        print(f"[{name}] {len(new_chunks)} chunks to embed")

        texts = [c["text"] for c in new_chunks]
        new_embeddings: list[list[float]] = []
        for i in range(0, len(texts), EMBED_BATCH_SIZE):
            new_embeddings.extend(rag.embed_batch(texts[i : i + EMBED_BATCH_SIZE]))
            print(f"  {min(i + EMBED_BATCH_SIZE, len(texts))}/{len(texts)}", flush=True)

        embeddings = np.vstack([embeddings, np.array(new_embeddings, dtype=np.float32)])
        chunks.extend(new_chunks)

    rag.save_index(embeddings.tolist(), chunks)
    print(f"Index now has {len(chunks)} chunks; saved to {rag.INDEX_DIR}")


if __name__ == "__main__":
    main()
