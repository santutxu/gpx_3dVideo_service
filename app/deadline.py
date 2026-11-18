"""Plazo cooperativo para los renders: todos los motores lo consultan fotograma a fotograma."""

from __future__ import annotations

import threading
import time

from .exceptions import RenderTimeoutError





class Deadline:
    """Deadline thread-local: cada worker tiene su propio plazo."""

    _local = threading.local()

    def __init__(self, timeout_s: int) -> None:
        self.timeout_s = timeout_s
        self.deadline: float | None = None

    def __enter__(self) -> Deadline:
        stack: list[Deadline] = getattr(self._local, "stack", None)  # type: ignore[attr-defined]
        if stack is None:
            stack = []
            self._local.stack = stack  # type: ignore[attr-defined]
        if self.timeout_s > 0:
            self.deadline = time.monotonic() + self.timeout_s
        else:
            self.deadline = None
        stack.append(self)
        return self

    def __exit__(self, *_: object) -> None:
        stack: list[Deadline] = getattr(self._local, "stack", [])  # type: ignore[attr-defined]
        if stack:
            stack.pop()

    @classmethod
    def current(cls) -> Deadline | None:
        stack: list[Deadline] = getattr(cls._local, "stack", None)  # type: ignore[attr-defined]
        if not stack:
            return None
        return stack[-1]


def check_deadline() -> None:
    """Lanza si el plazo del trabajo en curso ha vencido."""
    cur = Deadline.current()
    if cur is None or cur.deadline is None:
        return
    if time.monotonic() > cur.deadline:
        raise RenderTimeoutError(f"El render superó el tiempo máximo de {cur.timeout_s} s.")
