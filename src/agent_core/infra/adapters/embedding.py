from __future__ import annotations

from typing import Protocol

from google.adk.tools.spanner.utils import embed_contents_async


class EmbeddingService(Protocol):
    async def embed_text(self, text: str) -> list[float]:
        ...


class AdkEmbeddingService:
    """Embedding service backed by ADK helper utilities.

    Why this implementation: it uses ADK's own embedding helper path instead of
    introducing a separate third-party embedding stack.
    """

    def __init__(
        self,
        model_name: str,
        output_dimensionality: int | None = None,
    ) -> None:
        self.model_name = model_name
        self.output_dimensionality = output_dimensionality

    async def embed_text(self, text: str) -> list[float]:
        vectors = await embed_contents_async(
            vertex_ai_embedding_model_name=self.model_name,
            contents=[text],
            output_dimensionality=self.output_dimensionality,
        )
        if not vectors:
            msg = "embedding_generation_failed"
            raise RuntimeError(msg)
        return vectors[0]
