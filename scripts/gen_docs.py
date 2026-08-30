#!/usr/bin/env python3
"""Prepare the wiki markdown for an MkDocs build.

The wiki/ pages use GitHub-wiki link conventions (bare page names,
no ``.md``), which MkDocs can't resolve. This script copies them
into ``docs_build/`` and rewrites:

* ``](Page)`` / ``](Page#anchor)``   -> ``](Page.md...)``
* ``Home.md``                        -> ``index.md`` (MkDocs homepage)
* ``](../docs/...)``                 -> absolute GitHub blob URL
  (those files live in the code repo, not on the docs site)

wiki/README.md is the source-folder readme, not a wiki page — skipped.

Run from the repo root: ``python scripts/gen_docs.py``
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "wiki"
OUT = ROOT / "docs_build"
REPO_BLOB = "https://github.com/sabiut/polyglot-ai/blob/main"


def main() -> None:
    pages = sorted(p for p in SRC.glob("*.md") if p.name != "README.md")
    names = {p.stem for p in pages}

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    # ](Page) or ](Page#anchor) where Page is a known wiki page.
    link_re = re.compile(
        r"\]\((" + "|".join(re.escape(n) for n in sorted(names)) + r")(#[^)]*)?\)"
    )

    def rewrite(match: re.Match) -> str:
        page, anchor = match.group(1), match.group(2) or ""
        target = "index.md" if page == "Home" else f"{page}.md"
        return f"]({target}{anchor})"

    for page in pages:
        text = page.read_text(encoding="utf-8")
        text = link_re.sub(rewrite, text)
        text = text.replace("](../docs/", f"]({REPO_BLOB}/docs/")
        out_name = "index.md" if page.stem == "Home" else page.name
        (OUT / out_name).write_text(text, encoding="utf-8")
        print(f"  {page.name} -> {out_name}")

    print(f"{len(pages)} pages written to {OUT}")


if __name__ == "__main__":
    main()
