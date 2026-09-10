from __future__ import annotations


class ConfigError(ValueError):
    """A stable, redacted configuration error."""

    def __init__(self, code: str, message: str, *, json_pointer: str = "") -> None:
        self.code = code
        self.json_pointer = json_pointer or "/"
        super().__init__(f"{code} at {self.json_pointer}: {message}")


__all__ = ["ConfigError"]
