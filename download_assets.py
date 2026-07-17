#!/usr/bin/env python3
"""
One-time asset vendoring for QuantLab.

Downloads the three frontend dependencies (Tailwind, marked, Monaco Editor)
into static/vendor/ so the app runs 100% offline afterwards. Requires network
access only when you run *this* script.

Usage:  python3 download_assets.py
"""

import io
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

VENDOR = Path(__file__).resolve().parent / "static" / "vendor"
VENDOR.mkdir(parents=True, exist_ok=True)

TAILWIND_URL = "https://cdn.tailwindcss.com/3.4.16"
MARKED_URL = "https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js"
MONACO_TGZ = "https://registry.npmjs.org/monaco-editor/-/monaco-editor-0.45.0.tgz"


def fetch(url: str) -> bytes:
    print(f"  downloading {url} ...")
    req = urllib.request.Request(url, headers={"User-Agent": "quantlab-setup/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def main() -> int:
    print("[1/3] Tailwind CSS (standalone runtime)")
    (VENDOR / "tailwind.js").write_bytes(fetch(TAILWIND_URL))

    print("[2/3] marked (Markdown renderer)")
    (VENDOR / "marked.min.js").write_bytes(fetch(MARKED_URL))

    print("[3/3] Monaco Editor (npm tarball, extracting min/vs)")
    data = fetch(MONACO_TGZ)
    monaco_dir = VENDOR / "monaco"
    if monaco_dir.exists():
        shutil.rmtree(monaco_dir)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        prefix = "package/min/vs/"
        for member in tar.getmembers():
            if not member.name.startswith(prefix) or not member.isfile():
                continue
            rel = member.name[len(prefix):]
            if ".." in rel or rel.startswith("/"):
                continue  # defensive against path traversal
            dest = monaco_dir / "vs" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            src = tar.extractfile(member)
            dest.write_bytes(src.read())
    print(f"\nDone. Assets vendored into {VENDOR}")
    print("QuantLab is now fully offline-capable.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
