from types import SimpleNamespace

import numpy as np
import pytest

from zotwatch.interests.embedding_benchmark import MODELS, encode_existing_policy
from zotwatch.interests.ranking import encode_texts


class Tokenizer:
    def num_special_tokens_to_add(self):
        return 2
    def encode(self, text, *, add_special_tokens=True, truncation=False):
        return [int(token) for token in text.split()] + ([0, 0] if add_special_tokens else [])
    def decode(self, tokens, *, skip_special_tokens=True):
        return " ".join(map(str, tokens))


class Model:
    tokenizer = Tokenizer()
    max_seq_length = 130
    def get_sentence_embedding_dimension(self):
        return 3
    def encode(self, texts, **kwargs):
        return np.asarray([[sum(map(int, text.split())) % 7 + 1, len(text.split()),
            int(text.split()[0]) + 1] for text in texts], dtype=np.float32)


def test_only_two_fixed_challengers_and_the_pinned_baseline():
    assert len(MODELS) == 3
    assert [model.role for model in MODELS] == ["baseline", "multilingual challenger", "scientific title+abstract challenger"]
    assert all(len(model.revision) == 40 and set(model.revision) <= set("0123456789abcdef") for model in MODELS)


def test_batched_encoding_preserves_the_existing_all_token_policy():
    model = Model()
    texts = [" ".join(map(str, range(1, count+1))) for count in (10, 200, 401)]
    def encode(parts):
        matrix = model.encode(parts)
        return matrix / (np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-12)
    original = encode_texts(SimpleNamespace(model=model, encode=encode), texts)
    events = []
    current, info = encode_existing_policy(model, texts, batch_size=3, progress=lambda done, total: events.append((done, total)))
    assert np.allclose(current, original, atol=1e-12, rtol=0)
    assert info["record_count"] == 3 and info["chunk_count"] == 7
    assert events[-1] == (7, 7)
    assert np.allclose(np.linalg.norm(current, axis=1), 1)
    # Window batch size is a compute budget, not per-record truncation.
    repeated, _ = encode_existing_policy(model, texts, batch_size=1)
    assert np.array_equal(current, repeated)


@pytest.mark.parametrize("text", ["", "  ", None, "1" * 100001])
def test_invalid_semantic_input_is_not_silently_encoded(text):
    with pytest.raises(ValueError):
        encode_existing_policy(Model(), [text])


def test_roundtrip_overflow_is_split_uniformly_without_dropping_a_record():
    class ExpandingTokenizer(Tokenizer):
        def encode(self, text, *, add_special_tokens=True, truncation=False):
            return super().encode(text, add_special_tokens=add_special_tokens, truncation=truncation) + ([0, 0] if add_special_tokens else [])
    model = Model()
    model.tokenizer = ExpandingTokenizer()
    result, info = encode_existing_policy(model, [" ".join(map(str, range(1, 301)))])
    assert result.shape == (1, 3) and np.allclose(np.linalg.norm(result, axis=1), 1)
    assert info["record_count"] == 1 and info["chunk_count"] == 5 and info["roundtrip_split_count"] == 2
