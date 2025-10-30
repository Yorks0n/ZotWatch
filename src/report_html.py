from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import List

from jinja2 import Environment, Template

from .models import RankedWork

logger = logging.getLogger(__name__)

_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>ZotWatcher Report {{ generated_at }}</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 2rem; }
    h1 { border-bottom: 2px solid #444; padding-bottom: 0.5rem; }
    article { margin-bottom: 1.5rem; }
    .meta { color: #555; font-size: 0.9rem; }
  </style>
</head>
<body>
  <h1>ZotWatcher Recommendations</h1>
  <p>Generated at {{ generated_at }}</p>
  {% for work in works %}
    <article>
      <h2>{{ loop.index }}. <a href="{{ work.url or '#' }}">{{ work.title }}</a></h2>
      <div class="meta">
        <span>Label: {{ work.label }}</span> |
        <span>Score: {{ '%.3f'|format(work.score) }}</span> |
        <span>Similarity: {{ '%.3f'|format(work.similarity) }}</span> |
        <span>Published: {{ work.published.strftime('%Y-%m-%d') if work.published else 'Unknown' }}</span> |
        <span>Venue: {{ work.venue or 'Unknown' }}</span>
      </div>
      {% if work.abstract %}<p>{{ work.abstract }}</p>{% endif %}
      <div class="meta">
        Authors: {{ work.authors|join(', ') if work.authors else 'Unknown' }}
      </div>
    </article>
  {% endfor %}
</body>
</html>
"""


def render_html(works: List[RankedWork], output_path: Path | str) -> Path:
    env = Environment(autoescape=True)
    template: Template = env.from_string(_TEMPLATE)
    rendered = template.render(works=works, generated_at=datetime.utcnow().isoformat())
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8")
    logger.info("Wrote HTML report to %s", path)
    return path


__all__ = ["render_html"]
