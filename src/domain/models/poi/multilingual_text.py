"""MultilingualText: text recorded in more than one language. Which one
to show is a display decision, not fixed at data time."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MultilingualText:
    by_language: dict[str, str] = field(default_factory=dict)

    def get(self, *preferred: str) -> str | None:
        """The text in the first of `preferred` actually present, or
        None -- e.g. `.get("en", "de")`."""
        for lang in preferred:
            if lang in self.by_language:
                return self.by_language[lang]
        return None

    def __bool__(self) -> bool:
        return bool(self.by_language)
