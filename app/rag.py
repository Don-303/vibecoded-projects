"""Retrieval over locally-cloned reference notebook corpora (QuantRocket
lectures, QuantEcon notebook gallery and lecture series). Fully offline once
the corpora are cloned and the index is built by scripts/build_rag_index.py.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from ollama import AsyncClient, Client

EMBED_MODEL = "nomic-embed-text"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = PROJECT_ROOT / "corpus"
INDEX_DIR = PROJECT_ROOT / "data" / "rag_index"
EMBEDDINGS_PATH = INDEX_DIR / "embeddings.npy"
CHUNKS_PATH = INDEX_DIR / "chunks.json"

# (short label used in citations/lookups, git url, subdirectory to search, file glob)
SOURCES = [
    ("quantrocket-lectures", "https://github.com/quantrocket-codeload/quant-finance-lectures.git", "quant_finance_lectures", "*.ipynb"),
    ("quantecon-notebook-gallery", "https://github.com/QuantEcon/notebook-gallery.git", "ipynb", "*.ipynb"),
    ("quantecon-lecture-python-intro", "https://github.com/QuantEcon/lecture-python-intro.git", "lectures", "*.md"),
    ("quantecon-lecture-python-programming", "https://github.com/QuantEcon/lecture-python-programming.git", "lectures", "*.md"),
    ("quantecon-lecture-python", "https://github.com/QuantEcon/lecture-python.myst.git", "lectures", "*.md"),
    ("quantecon-lecture-python-advanced", "https://github.com/QuantEcon/lecture-python-advanced.myst.git", "lectures", "*.md"),
    ("quantecon-lecture-datascience", "https://github.com/QuantEcon/lecture-datascience.myst.git", "lectures", "*.md"),
    ("quantecon-continuous-time-mcs", "https://github.com/QuantEcon/continuous_time_mcs.git", "lectures", "*.md"),
]

FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
RAW_HTML_BLOCK_RE = re.compile(r"```\{raw\}\s*html.*?```", re.DOTALL)

_sync_client = Client(host="http://127.0.0.1:11434")
_async_client = AsyncClient(host="http://127.0.0.1:11434")


@dataclass
class Chunk:
    text: str
    source_repo: str
    source_file: str
    title: str


_cache: tuple[np.ndarray, list[Chunk]] | None = None


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Synchronous, batched embedding -- used by the offline index-build script."""
    resp = _sync_client.embed(model=EMBED_MODEL, input=texts)
    return [list(e) for e in resp.embeddings]


def save_index(embeddings: list[list[float]], chunks: list[dict]) -> None:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    np.save(EMBEDDINGS_PATH, np.array(embeddings, dtype=np.float32))
    CHUNKS_PATH.write_text(json.dumps(chunks))


def _load() -> tuple[np.ndarray, list[Chunk]] | None:
    global _cache
    if _cache is not None:
        return _cache
    if not EMBEDDINGS_PATH.exists() or not CHUNKS_PATH.exists():
        return None
    embeddings = np.load(EMBEDDINGS_PATH)
    raw = json.loads(CHUNKS_PATH.read_text())
    chunks = [Chunk(**c) for c in raw]
    _cache = (embeddings, chunks)
    return _cache


def is_available() -> bool:
    return _load() is not None


async def retrieve(query: str, top_k: int = 4, min_similarity: float = 0.4) -> list[Chunk]:
    loaded = _load()
    if loaded is None:
        return []
    embeddings, chunks = loaded
    try:
        resp = await _async_client.embed(model=EMBED_MODEL, input=query)
    except Exception:
        return []
    q = np.array(resp.embeddings[0], dtype=np.float32)

    q_norm = q / (np.linalg.norm(q) + 1e-8)
    e_norm = embeddings / (np.linalg.norm(embeddings, axis=1, keepdims=True) + 1e-8)
    sims = e_norm @ q_norm

    top_idx = np.argsort(-sims)[:top_k]
    return [chunks[i] for i in top_idx if sims[i] >= min_similarity]


def find_source_file(source_repo: str, source_file: str) -> Path | None:
    repo_dir = CORPUS_DIR / source_repo
    if not repo_dir.exists():
        return None
    matches = list(repo_dir.rglob(source_file))
    return matches[0] if matches else None


def _notebook_full_text(path: Path, max_chars: int) -> str:
    nb = json.loads(path.read_text(errors="ignore"))
    parts = []
    for cell in nb.get("cells", []):
        source = cell.get("source", "")
        text = "".join(source) if isinstance(source, list) else source
        text = text.strip()
        if not text:
            continue
        parts.append(f"```python\n{text}\n```" if cell.get("cell_type") == "code" else text)
    return "\n\n".join(parts)[:max_chars]


def _markdown_full_text(path: Path, max_chars: int) -> str:
    text = path.read_text(errors="ignore")
    text = FRONTMATTER_RE.sub("", text)
    text = RAW_HTML_BLOCK_RE.sub("", text)
    return text.strip()[:max_chars]


def load_source_text(source_repo: str, source_file: str, max_chars: int = 3500) -> str | None:
    """Full (cleaned, length-capped) text of one lecture file, for direct
    injection into the tutor's prompt -- not a retrieval chunk."""
    path = find_source_file(source_repo, source_file)
    if path is None:
        return None
    try:
        if path.suffix == ".ipynb":
            return _notebook_full_text(path, max_chars)
        return _markdown_full_text(path, max_chars)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None
