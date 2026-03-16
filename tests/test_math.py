"""Tests for Gini coefficient, ideology embeddings, and compass projection."""

import numpy as np
import pytest

from src.server import _gini
from src.ideology import (
    _fallback_embed_text,
    bytes_to_embedding,
    compute_compass_position,
    cosine_similarity,
    embedding_to_bytes,
    generate_ideology_name,
    update_agent_ideology,
    get_society_average_ideology,
    EMBEDDING_DIM,
)


# ---------------------------------------------------------------------------
# Gini coefficient
# ---------------------------------------------------------------------------


class TestGini:
    def test_perfect_equality(self) -> None:
        assert _gini([100, 100, 100, 100]) == 0.0

    def test_maximal_inequality(self) -> None:
        g = _gini([0, 0, 0, 1000])
        assert g > 0.7

    def test_empty_list(self) -> None:
        assert _gini([]) == 0.0

    def test_single_agent(self) -> None:
        assert _gini([500]) == 0.0

    def test_all_zeros(self) -> None:
        assert _gini([0, 0, 0]) == 0.0

    def test_moderate_inequality(self) -> None:
        g = _gini([10, 10, 10, 500, 500, 500])
        assert 0.2 < g < 0.8

    def test_oligarchy_like_distribution(self) -> None:
        g = _gini([500, 500, 500, 10])
        assert g > 0.15

    def test_democracy_like_distribution(self) -> None:
        g = _gini([100, 100, 100, 100])
        assert g == 0.0

    def test_gini_increases_with_inequality(self) -> None:
        equal = _gini([100, 100, 100, 100])
        mild = _gini([80, 90, 110, 120])
        severe = _gini([10, 10, 10, 970])
        assert equal < mild < severe


# ---------------------------------------------------------------------------
# Embedding serialization
# ---------------------------------------------------------------------------


class TestEmbeddingSerialization:
    def test_round_trip(self) -> None:
        original = np.random.randn(EMBEDDING_DIM).astype(np.float32)
        restored = bytes_to_embedding(embedding_to_bytes(original))
        np.testing.assert_array_almost_equal(original, restored)

    def test_correct_dimension(self) -> None:
        emb = _fallback_embed_text("test text")
        assert emb.shape == (EMBEDDING_DIM,)

    def test_fallback_is_deterministic(self) -> None:
        a = _fallback_embed_text("the same input twice")
        b = _fallback_embed_text("the same input twice")
        np.testing.assert_array_equal(a, b)

    def test_different_texts_different_embeddings(self) -> None:
        a = _fallback_embed_text("free markets and private property")
        b = _fallback_embed_text("collective ownership and redistribution")
        assert not np.allclose(a, b)

    def test_fallback_is_normalized(self) -> None:
        emb = _fallback_embed_text("some meaningful political text")
        norm = np.linalg.norm(emb)
        assert abs(norm - 1.0) < 1e-5

    def test_empty_text_returns_zero_vector(self) -> None:
        emb = _fallback_embed_text("")
        assert np.allclose(emb, 0.0)


# ---------------------------------------------------------------------------
# Cosine similarity
# ---------------------------------------------------------------------------


class TestCosineSimilarity:
    def test_identical_vectors(self) -> None:
        v = np.array([1.0, 0.0, 0.0])
        assert abs(cosine_similarity(v, v) - 1.0) < 1e-6

    def test_orthogonal_vectors(self) -> None:
        a = np.array([1.0, 0.0, 0.0])
        b = np.array([0.0, 1.0, 0.0])
        assert abs(cosine_similarity(a, b)) < 1e-6

    def test_opposite_vectors(self) -> None:
        a = np.array([1.0, 0.0])
        b = np.array([-1.0, 0.0])
        assert cosine_similarity(a, b) < -0.99


# ---------------------------------------------------------------------------
# Compass projection
# ---------------------------------------------------------------------------


class TestCompass:
    def test_returns_expected_keys(self) -> None:
        emb = _fallback_embed_text("markets and individual freedom")
        result = compute_compass_position(emb)
        assert "x" in result
        assert "y" in result
        assert "ideology_name" in result
        assert "raw_similarities" in result

    def test_x_and_y_bounded(self) -> None:
        for text in [
            "collective ownership and redistribution",
            "free markets and capitalism",
            "strong state control",
            "individual liberty and autonomy",
        ]:
            emb = _fallback_embed_text(text)
            result = compute_compass_position(emb)
            assert -1.0 <= result["x"] <= 1.0
            assert -1.0 <= result["y"] <= 1.0


class TestIdeologyName:
    def test_centrist(self) -> None:
        assert generate_ideology_name(0.0, 0.0) == "Centrist"

    def test_far_left_authoritarian(self) -> None:
        name = generate_ideology_name(-0.6, 0.6)
        assert "Socialist" in name
        assert "Authoritarian" in name

    def test_far_right_libertarian(self) -> None:
        name = generate_ideology_name(0.6, -0.6)
        assert "Capitalist" in name
        assert "Anarcho" in name


# ---------------------------------------------------------------------------
# Agent ideology update and society average
# ---------------------------------------------------------------------------


class TestIdeologyTracking:
    def test_first_embedding_sets_ideology(self, db) -> None:
        from src import server as srv

        result = srv.join_society("Ideolog", consent=True)
        aid = result["agent_id"]

        emb = _fallback_embed_text("collective ownership and workers")
        updated = update_agent_ideology(srv.db, aid, emb)
        np.testing.assert_array_almost_equal(updated, emb)

    def test_moving_average_blends(self, db) -> None:
        from src import server as srv

        result = srv.join_society("Blender", consent=True)
        aid = result["agent_id"]

        emb1 = _fallback_embed_text("collective ownership")
        update_agent_ideology(srv.db, aid, emb1)

        emb2 = _fallback_embed_text("free markets")
        updated = update_agent_ideology(srv.db, aid, emb2)

        assert not np.allclose(updated, emb1)
        assert not np.allclose(updated, emb2)

    def test_society_average_none_without_data(self, db) -> None:
        from src import server as srv

        avg = get_society_average_ideology(srv.db, "democracy_1")
        assert avg is None

    def test_society_average_computed(self, db) -> None:
        from src import server as srv
        import random

        old_choice = random.choice
        random.choice = lambda seq: "democracy"
        try:
            r1 = srv.join_society("A1", consent=True)
            r2 = srv.join_society("A2", consent=True)
        finally:
            random.choice = old_choice

        update_agent_ideology(
            srv.db, r1["agent_id"], _fallback_embed_text("markets and freedom")
        )
        update_agent_ideology(
            srv.db, r2["agent_id"], _fallback_embed_text("collective ownership")
        )
        srv.db.commit()

        avg = get_society_average_ideology(srv.db, "democracy_1")
        assert avg is not None
        assert avg.shape == (EMBEDDING_DIM,)
