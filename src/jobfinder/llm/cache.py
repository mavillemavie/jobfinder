from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from jobfinder import paths
from jobfinder.llm.base import LLMProvider, Tier

logger = logging.getLogger(__name__)


class CachedLLM:
    """Content-hash cache: identical (task, tier, system, user, schema) → stored JSON."""

    def __init__(self, inner: LLMProvider, cache_dir: Path | None = None) -> None:
        self.inner = inner
        self.name = inner.name
        self.cache_dir = cache_dir or paths.llm_cache_dir()
        self.hits = 0
        self.misses = 0
        self._bypass = False

    def bypass_next(self) -> None:
        self._bypass = True

    def _key(self, task: str, tier: str, system: str, user: str, schema: dict[str, Any]) -> Path:
        h = hashlib.sha256(
            json.dumps([self.name, task, tier, system, user, schema], sort_keys=True).encode()
        ).hexdigest()
        return self.cache_dir / task / f"{h}.json"

    def complete_json(
        self,
        *,
        task: str,
        system: str,
        user: str,
        schema: dict[str, Any],
        tier: Tier = "fast",
        language: str = "en",
    ) -> dict[str, Any]:
        path = self._key(task, tier, system, user, schema)
        if path.exists() and not self._bypass:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self.hits += 1
                return data
            except (ValueError, OSError) as e:
                logger.warning(f"Corrupt cache entry at {path}: {e}. Treating as miss.")
                path.unlink(missing_ok=True)
        self._bypass = False
        self.misses += 1
        data = self.inner.complete_json(
            task=task, system=system, user=user, schema=schema, tier=tier, language=language
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: write to temp file, then replace
        with tempfile.NamedTemporaryFile(
            mode="w", dir=path.parent, delete=False, suffix=".tmp", encoding="utf-8"
        ) as tmp:
            json.dump(data, tmp, ensure_ascii=False)
            tmp_path = tmp.name
        os.replace(tmp_path, str(path))
        return data
