from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class Parameter:
    key: str
    value: Any
    unit: str
    category: str
    description: str

    def matches(self, query: str, category: str | None) -> bool:
        normalized_query = query.lower().strip()
        if category and category != "all" and self.category != category:
            return False
        if not normalized_query:
            return True
        haystack = f"{self.key} {self.description} {self.category}".lower()
        return normalized_query in haystack
