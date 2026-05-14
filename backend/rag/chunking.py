import re


class RecursiveChunker:
    """Recursive paragraph/sentence chunker with character-window fallback."""

    def __init__(self, chunk_size: int = 320, chunk_overlap: int = 50):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def split(self, text: str) -> list[str]:
        separators = ["\n\n", "\n", "。", "！", "？", ". ", " ", ""]
        return self._split_recursive(text, separators)

    def _split_recursive(self, text: str, separators: list[str]) -> list[str]:
        if len(text) <= self.chunk_size:
            return [text] if text.strip() else []

        separator = separators[0] if separators else ""
        if not separator:
            return self._sliding_character_chunks(text)

        parts = text.split(separator)
        chunks = []
        current = ""

        for part in parts:
            piece = part if not current else current + separator + part
            if len(piece) <= self.chunk_size:
                current = piece
                continue

            if current:
                chunks.append(current)

            if len(part) > self.chunk_size:
                chunks.extend(self._split_recursive(part, separators[1:]))
                current = ""
            else:
                current = part

        if current:
            chunks.append(current)

        return [chunk for chunk in chunks if chunk.strip()]

    def _sliding_character_chunks(self, text: str) -> list[str]:
        step = max(1, self.chunk_size - self.chunk_overlap)
        return [
            text[i:i + self.chunk_size]
            for i in range(0, len(text), step)
            if text[i:i + self.chunk_size].strip()
        ]


class SemanticChunker:
    """Semantic chunker based on adjacent sentence embedding similarity."""

    def __init__(self, similarity_threshold: float = 0.3, min_chunk_size: int = 100, max_chunk_size: int = 1000):
        self.similarity_threshold = similarity_threshold
        self.min_chunk_size = min_chunk_size
        self.max_chunk_size = max_chunk_size

    def split(self, text: str) -> list[str]:
        sentences = re.split(r'(?<=[。！？.!?\n])\s*', text)
        sentences = [s.strip() for s in sentences if s.strip()]

        if not sentences:
            return []

        from backend.rag.embeddings import get_embedding_client
        embedder = get_embedding_client()
        embeddings = embedder.embed(sentences)

        chunks = []
        current_sentences = [sentences[0]]
        current_embedding = embeddings[0]

        for i in range(1, len(sentences)):
            similarity = self._cosine_sim(current_embedding, embeddings[i])
            current_len = sum(len(s) for s in current_sentences)

            if similarity < self.similarity_threshold or current_len + len(sentences[i]) > self.max_chunk_size:
                if current_len >= self.min_chunk_size:
                    chunks.append(" ".join(current_sentences))
                    current_sentences = [sentences[i]]
                    current_embedding = embeddings[i]
                else:
                    current_sentences.append(sentences[i])
                    current_embedding = self._average_embedding(
                        current_embedding,
                        embeddings[i],
                        len(current_sentences),
                    )
            else:
                current_sentences.append(sentences[i])
                current_embedding = self._average_embedding(
                    current_embedding,
                    embeddings[i],
                    len(current_sentences),
                )

        if current_sentences:
            chunks.append(" ".join(current_sentences))

        return chunks

    @staticmethod
    def _cosine_sim(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x ** 2 for x in a) ** 0.5
        norm_b = sum(x ** 2 for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    @staticmethod
    def _average_embedding(old: list[float], new: list[float], count: int) -> list[float]:
        return [(old[j] * (count - 1) + new[j]) / count for j in range(len(old))]


class SlidingWindowChunker:
    """Sliding window chunker for context retrieval."""

    def __init__(self, window_size: int = 256, stride: int = 128):
        self.window_size = window_size
        self.stride = stride

    def split(self, text: str) -> list[dict]:
        """Returns chunks with window metadata."""
        chunks = []
        start = 0
        idx = 0
        while start < len(text):
            window_text = text[start:start + self.window_size]
            chunks.append({
                "content": window_text,
                "index": idx,
                "start": start,
                "end": start + len(window_text),
            })
            start += self.stride
            idx += 1
        return chunks


def get_chunker(strategy: str = "recursive", **kwargs):
    if strategy == "semantic":
        return SemanticChunker(**kwargs)
    if strategy == "sliding_window":
        return SlidingWindowChunker(**kwargs)
    return RecursiveChunker(**kwargs)
