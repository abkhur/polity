"""Ideology tracking via sentence-transformer embeddings."""

from __future__ import annotations

import logging
import sqlite3
from hashlib import sha256
from typing import TYPE_CHECKING, Any, cast

import numpy as np

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer as SentenceTransformerType
else:
    SentenceTransformerType = Any

try:
    from sentence_transformers import SentenceTransformer
except ModuleNotFoundError:
    SentenceTransformer = None  # type: ignore[assignment]

logger = logging.getLogger("polity.ideology")

EMBEDDING_DIM = 384
MOVING_AVERAGE_ALPHA = 0.3

# Reference texts for political compass axes
REFERENCE_TEXTS = {
    "left": "collective ownership, economic equality, wealth redistribution, workers' control",
    "right": "free markets, private property, individual wealth, capitalism",
    "authoritarian": "strong state control, centralized power, obedience to authority",
    "libertarian": "individual freedom, minimal government, personal autonomy, liberty",
}

_model: SentenceTransformerType | None = None
_reference_embeddings: dict[str, np.ndarray] | None = None
_use_fallback_embeddings = False


def _get_model() -> SentenceTransformerType:
    global _model, _use_fallback_embeddings
    if _model is None:
        if SentenceTransformer is None:
            raise RuntimeError("sentence-transformers is not installed")
        try:
            logger.info("Loading sentence-transformer model...")
            _model = SentenceTransformer("all-MiniLM-L6-v2")
            logger.info("Model loaded.")
        except Exception as exc:
            _use_fallback_embeddings = True
            logger.warning("Falling back to local hash embeddings: %s", exc)
            raise RuntimeError("Falling back to local hash embeddings") from exc
    return cast(SentenceTransformerType, _model)


def _fallback_embed_text(text: str) -> np.ndarray:
    """Deterministic local fallback when sentence-transformers is unavailable."""
    vector = np.zeros(EMBEDDING_DIM, dtype=np.float32)
    tokens = text.lower().split()
    if not tokens:
        return vector

    for token in tokens:
        digest = sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % EMBEDDING_DIM
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign

    norm = np.linalg.norm(vector)
    if norm > 0:
        vector = vector / norm
    return vector


def _get_reference_embeddings() -> dict[str, np.ndarray]:
    global _reference_embeddings
    if _reference_embeddings is None:
        if SentenceTransformer is None or _use_fallback_embeddings:
            _reference_embeddings = {
                key: _fallback_embed_text(text)
                for key, text in REFERENCE_TEXTS.items()
            }
        else:
            try:
                model = _get_model()
                _reference_embeddings = {
                    key: model.encode(text, normalize_embeddings=True)
                    for key, text in REFERENCE_TEXTS.items()
                }
            except RuntimeError:
                _reference_embeddings = {
                    key: _fallback_embed_text(text)
                    for key, text in REFERENCE_TEXTS.items()
                }
    return _reference_embeddings


def embed_text(text: str) -> np.ndarray:
    """Embed a text message into a 384-dimensional vector."""
    if SentenceTransformer is None or _use_fallback_embeddings:
        return _fallback_embed_text(text)
    try:
        model = _get_model()
        return model.encode(text, normalize_embeddings=True)
    except RuntimeError:
        return _fallback_embed_text(text)


def embedding_to_bytes(embedding: np.ndarray) -> bytes:
    """Serialize a numpy embedding to bytes for SQLite storage."""
    return embedding.astype(np.float32).tobytes()


def bytes_to_embedding(data: bytes) -> np.ndarray:
    """Deserialize bytes from SQLite back to a numpy embedding."""
    return np.frombuffer(data, dtype=np.float32)


def update_agent_ideology(db: sqlite3.Connection, agent_id: str, new_embedding: np.ndarray) -> np.ndarray:
    """Update an agent's ideology embedding using exponential moving average (alpha=0.3).

    If the agent has no existing embedding, the new embedding becomes their ideology.
    Otherwise: updated = alpha * new + (1 - alpha) * old, then normalized.
    """
    row = db.execute("SELECT ideology_embedding FROM agents WHERE id = ?", (agent_id,)).fetchone()
    existing_blob = row["ideology_embedding"] if row else None

    if existing_blob is None:
        updated = new_embedding.copy()
    else:
        existing = bytes_to_embedding(existing_blob)
        updated = MOVING_AVERAGE_ALPHA * new_embedding + (1 - MOVING_AVERAGE_ALPHA) * existing
        # Re-normalize after blending
        norm = np.linalg.norm(updated)
        if norm > 0:
            updated = updated / norm

    db.execute(
        "UPDATE agents SET ideology_embedding = ? WHERE id = ?",
        (embedding_to_bytes(updated), agent_id),
    )
    return updated


def get_society_average_ideology(db: sqlite3.Connection, society_id: str) -> np.ndarray | None:
    """Compute the mean ideology embedding across all active agents in a society.

    Returns None if no agents have ideology embeddings yet.
    """
    rows = db.execute(
        "SELECT ideology_embedding FROM agents WHERE society_id = ? AND status = 'active' AND ideology_embedding IS NOT NULL",
        (society_id,),
    ).fetchall()

    if not rows:
        return None

    embeddings = [bytes_to_embedding(row["ideology_embedding"]) for row in rows]
    mean_emb = np.mean(embeddings, axis=0)
    norm = np.linalg.norm(mean_emb)
    if norm > 0:
        mean_emb = mean_emb / norm
    return mean_emb


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two vectors (assumed already normalized, but safe either way)."""
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-10))


def generate_ideology_name(x: float, y: float) -> str:
    """Generate ideology name from compass position."""
    # Economic descriptor
    if abs(x) < 0.2:
        econ = "Centrist"
    elif x < -0.5:
        econ = "Socialist"
    elif x < 0:
        econ = "Social Democratic"
    elif x < 0.5:
        econ = "Capitalist"
    else:
        econ = "Libertarian Capitalist"

    # Authority descriptor
    if abs(y) < 0.2:
        auth = ""
    elif y < -0.5:
        auth = "Anarcho-"
    elif y < 0:
        auth = "Libertarian "
    elif y < 0.5:
        auth = "Moderate "
    else:
        auth = "Authoritarian "

    return f"{auth}{econ}".strip()


def compute_compass_position(avg_embedding: np.ndarray) -> dict:
    """Map a society's average ideology embedding to political compass coordinates.

    x-axis: economic left (-1) to right (+1)
    y-axis: libertarian (-1) to authoritarian (+1)
    """
    refs = _get_reference_embeddings()

    sim_left = cosine_similarity(avg_embedding, refs["left"])
    sim_right = cosine_similarity(avg_embedding, refs["right"])
    sim_auth = cosine_similarity(avg_embedding, refs["authoritarian"])
    sim_lib = cosine_similarity(avg_embedding, refs["libertarian"])

    # x: right minus left, clamped to [-1, 1]
    x = float(np.clip(sim_right - sim_left, -1.0, 1.0))
    # y: authoritarian minus libertarian, clamped to [-1, 1]
    y = float(np.clip(sim_auth - sim_lib, -1.0, 1.0))

    return {
        "x": round(x, 4),
        "y": round(y, 4),
        "ideology_name": generate_ideology_name(x, y),
        "label_x": "economic left (-1) to right (+1)",
        "label_y": "libertarian (-1) to authoritarian (+1)",
        "raw_similarities": {
            "left": round(sim_left, 4),
            "right": round(sim_right, 4),
            "authoritarian": round(sim_auth, 4),
            "libertarian": round(sim_lib, 4),
        },
    }
