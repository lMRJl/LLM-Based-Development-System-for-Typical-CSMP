"""Zhipu Embedding API client with token-aware batching."""

from openai import OpenAI

from backend.config import get_settings


MODEL_LIMITS = {
    "embedding-2": {
        "max_tokens_per_text": 512,
        "max_total_tokens": 8000,
        "max_texts": None,
        "dimension": 1024,
        "dimensions_param": False,
    },
    "embedding-3": {
        "max_tokens_per_text": 3072,
        "max_total_tokens": None,
        "max_texts": 64,
        "dimension": 1024,
        "dimensions_param": True,
    },
}

TARGET_DIMENSION = 1024


class EmbeddingClient:
    """Embedding API client that respects model token and batch limits."""

    def __init__(self):
        settings = get_settings()
        self.client = OpenAI(
            api_key=settings.zhipu_api_key,
            base_url=settings.zhipu_base_url,
            timeout=45.0,
        )
        self.model = settings.zhipu_embedding_model
        limits = MODEL_LIMITS.get(self.model, MODEL_LIMITS["embedding-3"])
        self._max_tokens_per_text = limits["max_tokens_per_text"]
        self._max_total_tokens = limits["max_total_tokens"]
        self._max_texts = limits["max_texts"]
        self._dimension = TARGET_DIMENSION
        self._needs_dimensions_param = limits["dimensions_param"]

    @property
    def dimension(self) -> int:
        return TARGET_DIMENSION

    def _estimate_tokens(self, text: str) -> int:
        """Rough token estimation for mixed Chinese and ASCII text."""
        chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        other_chars = len(text) - chinese_chars
        return int(chinese_chars * 1.8 + other_chars * 0.4)

    def _truncate_for_embedding(self, text: str, estimated_tokens: int) -> str:
        """Keep head and tail when a text exceeds the embedding model limit."""
        target_tokens = self._max_tokens_per_text * 0.8
        ratio = target_tokens / max(estimated_tokens, 1)
        target_chars = max(1, int(len(text) * ratio))
        if target_chars >= len(text):
            return text

        head_len = max(1, int(target_chars * 0.7))
        tail_len = max(0, target_chars - head_len)
        if tail_len == 0:
            return text[:head_len]
        return text[:head_len] + "\n...\n" + text[-tail_len:]

    def _batch_texts(self, texts: list[str]) -> list[list[str]]:
        """Split texts into API-safe batches respecting token limits."""
        batches = []
        current_batch = []
        current_tokens = 0

        for text in texts:
            est = self._estimate_tokens(text)
            if est > self._max_tokens_per_text:
                text = self._truncate_for_embedding(text, est)
                est = self._estimate_tokens(text)

            would_exceed_total = self._max_total_tokens and current_tokens + est > self._max_total_tokens
            would_exceed_count = self._max_texts and len(current_batch) >= self._max_texts

            if (would_exceed_total or would_exceed_count) and current_batch:
                batches.append(current_batch)
                current_batch = []
                current_tokens = 0

            current_batch.append(text)
            current_tokens += est

        if current_batch:
            batches.append(current_batch)
        return batches

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings via Zhipu API with automatic token-aware batching."""
        if not texts:
            return []

        batches = self._batch_texts(texts)
        all_vectors = []

        try:
            for batch in batches:
                kwargs = {"model": self.model, "input": batch}
                if self._needs_dimensions_param:
                    kwargs["dimensions"] = TARGET_DIMENSION
                response = self.client.embeddings.create(**kwargs)
                vectors = [d.embedding for d in response.data]
                all_vectors.extend(vectors)

            return all_vectors
        except Exception as e:
            error_msg = str(e)
            if "401" in error_msg or "403" in error_msg:
                raise RuntimeError(
                    f"Zhipu Embedding API authentication failed. "
                    f"Check ZHIPU_API_KEY in your .env file. Details: {e}"
                ) from e
            if "404" in error_msg or "NotFound" in error_msg:
                raise RuntimeError(
                    f"Zhipu Embedding model '{self.model}' not found. "
                    f"Check ZHIPU_EMBEDDING_MODEL in your .env file. Details: {e}"
                ) from e
            raise RuntimeError(f"Zhipu Embedding API error: {e}") from e

    def embed_query(self, text: str) -> list[float]:
        """Generate embedding for a single query."""
        embeddings = self.embed([text])
        return embeddings[0]


_embedding_client: EmbeddingClient | None = None


def get_embedding_client() -> EmbeddingClient:
    global _embedding_client
    if _embedding_client is None:
        _embedding_client = EmbeddingClient()
    return _embedding_client
