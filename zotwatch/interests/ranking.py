from __future__ import annotations

import hashlib
import json
import numpy as np

from .contract import Profile, InterestError

SEMANTIC_THRESHOLD = 0.35
PRIORITY = {"low": 0.8, "normal": 1.0, "high": 1.2}
HORIZON = {"long_term": 1.0, "short_term": 1.1}
TEXT_POLICY = "topic-token-windows-128-mean-v1"


def encode_texts(vectorizer, texts):
    """Bounded tokenizer windows: every token participates; no name/author injection."""
    tokenizer = vectorizer.model.tokenizer
    window = min(128, int(vectorizer.model.max_seq_length) - tokenizer.num_special_tokens_to_add())
    if window < 1:
        raise InterestError("INTEREST_MODEL_INVALID")
    rows = []
    for text in texts:
        # Candidate API payload bounds are additionally enforced upstream.
        if len(text) > 100000:
            raise InterestError("INTEREST_TEXT_TOO_LARGE")
        tokens = tokenizer.encode(text, add_special_tokens=False, truncation=False)
        if not tokens:
            raise InterestError("INTEREST_TEXT_EMPTY")
        chunks = [tokenizer.decode(tokens[i:i + window], skip_special_tokens=True) for i in range(0, len(tokens), window)]
        # Decode/re-encode can expand token counts; explicitly reject rather than truncate.
        if any(len(tokenizer.encode(chunk, add_special_tokens=True, truncation=False)) > vectorizer.model.max_seq_length for chunk in chunks):
            raise InterestError("INTEREST_TEXT_INVALID")
        row = np.asarray(vectorizer.encode(chunks), dtype=np.float64).mean(axis=0)
        norm = np.linalg.norm(row)
        if not np.isfinite(row).all() or norm <= 1e-12:
            raise InterestError("INTEREST_VECTOR_INVALID")
        rows.append(row / norm)
    return np.asarray(rows)


def rank(profile: Profile, candidates, vectorizer, *, encode=encode_texts):
    active = sorted((t for t in profile.interests if t.status == "active"), key=lambda t: t.id)
    if not active or not candidates:
        return []
    topics = encode(vectorizer, [t.description for t in active])
    vectors = encode(vectorizer, ["\n".join(filter(None, [c.title, c.abstract])) for c in candidates])
    if topics.ndim != 2 or vectors.ndim != 2 or topics.shape[1] != vectors.shape[1] or not np.isfinite(topics).all() or not np.isfinite(vectors).all():
        raise InterestError("INTEREST_VECTOR_INVALID")
    similarities = np.clip(vectors @ topics.T, -1.0, 1.0)
    authors = {v.lower() for v in profile.preferences.authors}
    venues = {v.lower() for v in profile.preferences.venues}
    result = []
    for candidate, row in zip(candidates, similarities):
        matches = [(float(s) * PRIORITY[t.priority] * HORIZON[t.horizon], t, float(s))
                   for t, s in zip(active, row) if s >= SEMANTIC_THRESHOLD]
        if not matches:
            continue
        relevance, topic, raw = max(matches, key=lambda value: value[0])
        author = 0.02 if any(a.lower() in authors for a in candidate.authors) else 0.0
        venue = 0.05 if candidate.venue and candidate.venue.lower() in venues else 0.0
        bonus = min(0.05, author + venue)
        result.append({
            "work_key": candidate.doi or f"{candidate.source}:{candidate.identifier}",
            "title": candidate.title, "url": candidate.url,
            "matched_interest_id": topic.id, "raw_similarity": raw,
            "priority_multiplier": PRIORITY[topic.priority], "horizon_multiplier": HORIZON[topic.horizon],
            "topic_relevance": relevance, "author_bonus": author, "venue_bonus": venue,
            "preference_bonus": bonus, "score": relevance + bonus,
        })
    return sorted(result, key=lambda item: (-item["score"], item["work_key"]))


def semantic_input_hash(profile: Profile) -> str:
    data = [(t.id, t.description) for t in sorted(profile.interests, key=lambda t: t.id) if t.status == "active"]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
