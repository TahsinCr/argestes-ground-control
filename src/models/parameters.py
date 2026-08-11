from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.core.abstracts import Model


@dataclass(slots=True)
class Parameter(Model):
    key: str
    value: Any
    unit: str
    category: str
    description: str
    value_type: int | None = None
    default_value: Any | None = None

    def matches(self, query: str, category: str | None) -> bool:
        normalized_query = query.lower().strip()
        if category and category != "all" and self.category != category:
            return False
        if not normalized_query:
            return True
        haystack = f"{self.key} {self.description} {self.category}".lower()
        return normalized_query in haystack

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "unit": self.unit,
            "category": self.category,
            "description": self.description,
            "value_type": self.value_type,
            "default_value": self.default_value,
        }
