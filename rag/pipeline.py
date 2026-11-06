"""Campaign-scoped RAG helpers, kept separate from SciFact experiments."""
from __future__ import annotations

import re
import math
from threading import Lock

import numpy as np

PLATFORM_MODEL = "BAAI/bge-small-en-v1.5@5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
PLATFORM_MODEL_NAME = "BAAI/bge-small-en-v1.5"
PLATFORM_MODEL_REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
PLATFORM_CHUNKING = "bge_256_overlap_title_v1"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
MAX_CHUNK_TOKENS = 256


_MODEL = None
_MODEL_LOCK = Lock()
_ENCODE_LOCK = Lock()


def load_platform_model():
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    with _MODEL_LOCK:
        if _MODEL is not None:
            return _MODEL
        _MODEL = _initialize_platform_model()
    return _MODEL


def _initialize_platform_model():
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(PLATFORM_MODEL_NAME, revision=PLATFORM_MODEL_REVISION, device="cpu")
    model.default_prompt_name = None
    model.prompts = {}
    return model


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text.strip()) if part.strip()]


def chunk_text(text: str, title: str, tokenizer, max_tokens: int = MAX_CHUNK_TOKENS) -> list[dict]:
    """Use BGE-token-aware sentence packing with one-sentence overlap and title context."""
    def passage(parts: list[str]) -> str:
        body = " ".join(parts).strip()
        return f"{title.strip()}\n{body}" if title.strip() else body

    def count(value: str) -> int:
        return len(tokenizer(value, truncation=False, add_special_tokens=True, verbose=False)["input_ids"])

    if max_tokens < 1:
        raise ValueError("max_tokens must be positive")
    if not text.strip():
        return []
    sentences = []
    for sentence in _sentences(text):
        if count(passage([sentence])) <= max_tokens:
            sentences.append(sentence)
            continue
        words: list[str] = []
        for word in sentence.split():
            if count(passage([" ".join(words + [word])])) <= max_tokens:
                words.append(word)
            else:
                if words:
                    sentences.append(" ".join(words))
                if count(passage([word])) > max_tokens:
                    raise ValueError("A word and document title exceed the embedding token limit")
                words = [word]
        if words:
            sentences.append(" ".join(words))

    grouped: list[list[str]] = []
    current: list[str] = []
    for sentence in sentences:
        candidate = current + [sentence]
        if count(passage(candidate)) <= max_tokens:
            current = candidate
        else:
            if current:
                grouped.append(current)
            candidate = ([current[-1], sentence] if current else [sentence])
            current = candidate if count(passage(candidate)) <= max_tokens else [sentence]
    if current:
        grouped.append(current)
    return [
        {"chunk_index": index, "text": passage(parts), "token_count": count(passage(parts))}
        for index, parts in enumerate(grouped)
    ]


def embed_passages(texts: list[str]) -> np.ndarray:
    if not texts:
        return np.empty((0, 384), dtype=np.float32)
    with _ENCODE_LOCK:
        return load_platform_model().encode(
            texts, batch_size=16, normalize_embeddings=True, convert_to_numpy=True,
            show_progress_bar=False,
        )


def embed_query(text: str) -> np.ndarray:
    with _ENCODE_LOCK:
        return load_platform_model().encode(
            [QUERY_PREFIX + text], normalize_embeddings=True, convert_to_numpy=True,
            show_progress_bar=False,
        )[0]


def search_user_chunks(connection, owner_id: int, query: str, document_ids: list[str] | None = None,
                       top_k: int = 5, min_similarity: float = 0.70) -> list[dict]:
    """Search only the authenticated user's collection and the platform model version."""
    if not 1 <= top_k <= 10:
        raise ValueError("top_k must be between 1 and 10")
    if not math.isfinite(min_similarity) or not 0 <= min_similarity <= 1:
        raise ValueError("min_similarity must be between 0 and 1")
    dataset = f"social_studio_user_{owner_id}"
    vector = "[" + ",".join(str(float(value)) for value in embed_query(query)) + "]"
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.document_id, d.title, c.chunk_index, c.content,
                   1 - (e.embedding <=> %s::vector) AS similarity,
                   d.metadata->>'source_url' AS source_url
            FROM rag.chunks c
            JOIN rag.documents d ON d.dataset=c.dataset AND d.document_id=c.document_id
            JOIN rag.embeddings e ON e.chunk_id=c.id
            WHERE c.dataset=%s AND c.chunking_version=%s AND e.model_id=%s
              AND (cardinality(%s::text[]) = 0 OR c.document_id = ANY(%s::text[]))
            ORDER BY e.embedding <=> %s::vector
            LIMIT %s
            """,
            (vector, dataset, PLATFORM_CHUNKING, PLATFORM_MODEL, document_ids or [], document_ids or [], vector, top_k),
        )
        rows = cursor.fetchall()
    return [
        {"document_id": row[0], "title": row[1], "chunk_index": row[2], "text": row[3],
         "similarity": float(row[4]), "source_url": row[5]}
        for row in rows if math.isfinite(float(row[4])) and float(row[4]) >= min_similarity
    ]
