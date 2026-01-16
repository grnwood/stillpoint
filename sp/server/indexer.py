"""Placeholder search/index module.

Future work: watch the vault using watchdog, maintain a Tantivy or Whoosh index,
and answer /api/search queries in <100 ms.
"""

from __future__ import annotations

from typing import List


def stub_search(query: str, limit: int = 5) -> List[dict]:
    if not query:
        return []
    return [
        {
            "path": "/README/README.md",
            "title": "Search Coming Soon",
            "excerpt": "Indexing pipeline not implemented yet.",
            "score": 0.0,
        }
    ][:limit]
