from __future__ import annotations


class ParameterWriteError(RuntimeError):
    """A failed batch write with the subset confirmed by the vehicle."""

    def __init__(
        self,
        message: str,
        *,
        confirmed_values: dict[str, object] | None = None,
        uncertain_keys: tuple[str, ...] = (),
        unattempted_keys: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.confirmed_values = dict(confirmed_values or {})
        self.uncertain_keys = tuple(uncertain_keys)
        self.unattempted_keys = tuple(unattempted_keys)
