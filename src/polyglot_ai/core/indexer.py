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
            rel = file_path.relative_to(project_root).as_posix()
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

    @staticmethod
    def should_index(path: Path, root: Path) -> bool:
        """The one filter for what may enter the index (and so the prompt).

        Both the full build and per-save updates go through this. The
        save path used to skip it entirely, so saving ``.env`` (or a
        12 MB file under node_modules) indexed it, and a chat message
        sharing a few words with it pasted the contents into the system
        prompt sent to the provider.
        """
        from polyglot_ai.core.security import is_secret_file

        try:
            rel_parts = path.relative_to(root).parts
        except ValueError:
            return False
        if any(part in SKIP_DIRS for part in rel_parts):
            return False
        try:
            if not path.is_file() or path.is_symlink():
                return False
            if is_secret_file(path):
                return False  # Never index secrets
            if path.suffix not in CODE_EXTENSIONS and path.name not in CODE_EXTENSIONS:
                return False
            return path.stat().st_size <= MAX_FILE_SIZE
        except OSError:
            return False

    def _walk_files(self, root: Path) -> list[Path]:
        files = []
        try:
            for item in root.rglob("*"):
                if self.should_index(item, root):
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
        """Re-index a single file. Paths outside the project are ignored."""
        if not self._project_root:
            return
        try:
            rel = path.relative_to(self._project_root).as_posix()
        except ValueError:
            return
        if path.is_dir():
            return
        if not self.should_index(path, self._project_root):
            # Drop it if an earlier version was indexed (e.g. renamed to .env).
            self.remove_file(path)
            return
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
        """Drop a file — or, for a directory, everything under it — from the index."""
        if not self._project_root:
            return
        try:
            rel = path.relative_to(self._project_root).as_posix()
        except ValueError:
            return
        prefix = rel.rstrip("/") + "/"
        doomed = [r for r in self._files if r == rel or r.startswith(prefix)]
        if not doomed:
            return
        for r in doomed:
            self._tf.pop(r, None)
            self._files.discard(r)
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
