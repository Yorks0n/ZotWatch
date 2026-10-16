"""Approved P5C9 text checks, isolated from the P5C8 recommendation path."""
import re
from zotwatch.metadata import clean


def abstract_rejection(value):
    """Conservative metadata check, never manufacture an abstract from body text."""
    text = clean(value)
    if not text:
        return "missing"
    if re.search(r"\b(author contributions?|conflict of interest|publisher.s note|"
                 r"data availability statement|the author confirms being the sole contributor)\b", text, re.I):
        return "body_or_back_matter"
    if len(text) > 6000:
        return "overlong_unverified_text"
    if len(text) < 600 and re.search(r"[,;].*https?:", text, re.I):
        return "citation_only"
    return None


def verified_abstract(value):
    return clean(value) if abstract_rejection(value) is None else None
