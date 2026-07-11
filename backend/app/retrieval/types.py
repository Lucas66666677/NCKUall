from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RetrievedChunk:
    id: Any
    content: str
    source_type: str
    source_url: str | None
    source_title: str | None
    category: str
    chunk_index: int
    metadata_json: dict[str, Any]
    department_code: str | None
    department_name: str | None
    vector_distance: float | None = None
    lexical_score: float | None = None
    rrf_score: float = 0.0
    rerank_score: float | None = None

    @property
    def relevance_score(self) -> float:
        """Preserve the public cosine-similarity contract when available."""

        if self.vector_distance is not None:
            return max(
                0.0,
                min(1.0, 1.0 - float(self.vector_distance)),
            )
        # Cross-encoder scores determine order but are not calibrated
        # probabilities. Use one only when no vector score exists.
        if self.rerank_score is not None:
            return max(0.0, min(1.0, float(self.rerank_score)))
        if self.lexical_score is not None:
            return max(0.0, min(1.0, float(self.lexical_score)))
        return max(0.0, min(1.0, float(self.rrf_score)))
