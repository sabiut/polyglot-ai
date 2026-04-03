"""TF-IDF project indexer for RAG-style context augmentation."""

from __future__ import annotations

import logging
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

from polyglot_ai.constants import CODE_EXTENSIONS, MAX_FILE_SIZE, SKIP_DIRS

logger = logging.getLogger(__name__)


def _tokenize(text: str) -> list[str]:
    """Simple tokenizer: split on non-alphanumeric, split camelCase."""
    # Split camelCase
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    # Split on non-alphanumeric
    tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", text.lower())
    # Filter very short tokens
    return [t for t in tokens if len(t) > 2]


class ProjectIndexer:
    """In-memory TF-IDF index for project files."""

    def __init__(self) -> None:
        self._tf: dict[str, Counter] = {}  # file -> term frequencies
        self._idf: dict[str, float] = {}
        self._doc_count = 0
        self._files: set[str] = set()
        self._project_root: Path | None = None

    async def build_index(self, project_root: Path) -> None:
        """Build the index from scratch. Runs in background thread."""
        import asyncio

        self._project_root = project_root
        await asyncio.to_thread(self._build_sync, project_root)

    def _build_sync(self, project_root: Path) -> None:
        self._tf.clear()
        self._idf.clear()
        self._files.clear()

        df: dict[str, int] = defaultdict(int)  # document frequency

        files = list(self._walk_files(project_root))
        self._doc_count = len(files)

        for file_path in files:
            rel = str(file_path.relative_to(project_root))
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            tokens = _tokenize(content)
            tf = Counter(tokens)
            self._tf[rel] = tf
            self._files.add(rel)

            for term in set(tokens):
                df[term] += 1

        # Compute IDF
        if self._doc_count > 0:
            for term, freq in df.items():
                self._idf[term] = math.log(self._doc_count / (1 + freq))

        logger.info("Indexed %d files from %s", self._doc_count, project_root)

    def _walk_files(self, root: Path) -> list[Path]:
        from polyglot_ai.core.security import is_secret_file

        files = []
        try:
            for item in root.rglob("*"):
                if any(part in SKIP_DIRS for part in item.parts):
                    continue
                if not item.is_file():
                    continue
                if is_secret_file(item):
                    continue  # Never index secrets
                if item.suffix in CODE_EXTENSIONS or item.name in CODE_EXTENSIONS:
                    if item.stat().st_size <= MAX_FILE_SIZE:
                        files.append(item)
        except OSError:
            pass
        return files

    def query(self, text: str, top_k: int = 5) -> list[tuple[str, float]]:
        """Return top-k most relevant file paths with scores."""
        query_tokens = _tokenize(text)
        if not query_tokens:
            return []

        # Compute query TF-IDF
        query_tf = Counter(query_tokens)
        query_vec: dict[str, float] = {}
        for term, count in query_tf.items():
            idf = self._idf.get(term, 0)
            query_vec[term] = count * idf

        # Score each document via dot product
        scores: list[tuple[str, float]] = []
        for rel, doc_tf in self._tf.items():
            score = 0.0
            for term, q_weight in query_vec.items():
                if term in doc_tf:
                    doc_weight = doc_tf[term] * self._idf.get(term, 0)
                    score += q_weight * doc_weight
            if score > 0:
                scores.append((rel, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    def update_file(self, path: Path) -> None:
        """Re-index a single file."""
        if not self._project_root:
            return
        rel = str(path.relative_to(self._project_root))
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            self.remove_file(path)
            return

        tokens = _tokenize(content)
        self._tf[rel] = Counter(tokens)
        self._files.add(rel)
        # Rebuild IDF (simple approach for incremental)
        self._rebuild_idf()

    def remove_file(self, path: Path) -> None:
        if not self._project_root:
            return
        rel = str(path.relative_to(self._project_root))
        self._tf.pop(rel, None)
        self._files.discard(rel)
        self._rebuild_idf()

    def _rebuild_idf(self) -> None:
        df: dict[str, int] = defaultdict(int)
        self._doc_count = len(self._tf)
        for tf in self._tf.values():
            for term in tf:
                df[term] += 1
        self._idf.clear()
        if self._doc_count > 0:
            for term, freq in df.items():
                self._idf[term] = math.log(self._doc_count / (1 + freq))

    @property
    def is_ready(self) -> bool:
        return self._doc_count > 0
