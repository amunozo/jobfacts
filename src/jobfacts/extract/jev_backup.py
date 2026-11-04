"""Jev through OpenRouter as a backup for TypeSafe's own API.

OpenRouter serves the same model (`typesafe/jev-1.14`, provider TypeSafe, same price) and
takes the request body that the TypeSafe SDK builds for System One, so a call that failed on
TypeSafe is repeated there and its answers are stored as normal Jev results. The response
is parsed with the SDK's own `SystemOneResponse`, so callers read it the same way.
"""

import os
import threading
from typing import Any

import httpx
import httpx2
from typesafe_sdk import (
    SystemOneResponse,
    TypeSafeAPIConnectionError,
    TypeSafeAPIError,
    TypeSafeError,
)
from typesafe_sdk._core.json import serialize
from typesafe_sdk._core.questions import normalize_questions

OPENROUTER_URL = "https://openrouter.ai/api/alpha/decisions"
OPENROUTER_MODEL = "typesafe/jev-1.14"
KEY_ENV = "OPENROUTER_API_KEY"
ROUTE = "openrouter"
TIMEOUT = 120.0
BREAKER_FAILURES = 3  # consecutive primary availability failures before it is skipped
# Statuses that say TypeSafe itself is unusable (key, quota, rate, outage), not our request.
UNAVAILABLE_STATUSES = {401, 402, 403, 408, 429}
ERROR_BODY_CHARS = 300


def is_unavailable(error: Exception) -> bool:
    """True if the error says the primary service is unusable (connection, timeout, key, quota,
    rate limit, 5xx). Errors caused by one request (400, 404, 413, 422, a request the SDK
    refuses before sending) are False: they still go to the backup, but do not trip the
    breaker, because the same request would fail anywhere."""
    if isinstance(error, TypeSafeAPIConnectionError):
        return True
    if isinstance(error, TypeSafeAPIError):
        status = getattr(error, "status", None) or 0
        return status in UNAVAILABLE_STATUSES or status >= 500
    if isinstance(error, TypeSafeError):
        return False
    return True  # unknown failure (network stack, OS): treat as unavailable


class BackupError(RuntimeError):
    """The OpenRouter call failed (non-2xx status, bad body or network error)."""


class OpenRouterJev:
    """`system_one(state, questions, model)` against OpenRouter; the model argument is ignored
    because the backup always asks for OPENROUTER_MODEL. Responses carry `route == "openrouter"`."""

    def __init__(
        self, api_key: str, http: httpx.Client | None = None, timeout: float = TIMEOUT
    ) -> None:
        self._key = api_key
        self._http = http or httpx.Client(timeout=timeout)

    @classmethod
    def from_env(cls) -> "OpenRouterJev | None":
        """None when OPENROUTER_API_KEY is not set (the backup is then not configured)."""
        key = os.environ.get(KEY_ENV, "").strip()
        return cls(key) if key else None

    def system_one(self, state: Any, questions: Any, model: str | None = None) -> SystemOneResponse:
        body = serialize(
            {"state": state, "model": OPENROUTER_MODEL, "questions": normalize_questions(questions)}
        )
        try:
            r = self._http.post(
                OPENROUTER_URL,
                content=body,
                headers={
                    "Authorization": f"Bearer {self._key}",
                    "Content-Type": "application/json",
                },
            )
        except httpx.HTTPError as error:
            raise BackupError(
                f"OpenRouter request failed: {type(error).__name__}: {error}"
            ) from error
        if not r.is_success:
            raise BackupError(f"OpenRouter HTTP {r.status_code}: {r.text[:ERROR_BODY_CHARS]}")
        try:
            response = SystemOneResponse.from_http_response(
                httpx2.Response(r.status_code, content=r.content)
            )
        except Exception as error:
            raise BackupError(f"OpenRouter response not understood: {error}") from error
        response.__dict__["route"] = ROUTE  # the SDK keeps its own metadata in __dict__ too
        return response


class FallbackClient:
    """Try `primary`; if it raises, call `backup`. After BREAKER_FAILURES consecutive primary
    availability failures (`is_unavailable`) the primary is skipped for the rest of the
    process; request errors go to the backup without counting. Thread-safe."""

    def __init__(self, primary: Any, backup: Any) -> None:
        self.primary, self.backup = primary, backup
        self._lock = threading.Lock()
        self._failures = 0
        self.primary_disabled = False  # sticky: set once, never reset
        self.backup_calls = 0

    def system_one(self, state: Any, questions: Any, model: str | None = None) -> Any:
        primary_error: Exception | None = None
        if not self.primary_disabled:
            try:
                response = self.primary.system_one(state=state, questions=questions, model=model)
            except Exception as error:
                primary_error = error
                if is_unavailable(error):
                    with self._lock:
                        self._failures += 1
                        if self._failures >= BREAKER_FAILURES:
                            self.primary_disabled = True
            else:
                with self._lock:
                    self._failures = 0
                return response
        try:
            response = self.backup.system_one(state=state, questions=questions, model=model)
        except Exception as backup_error:
            first = (
                f"primary {type(primary_error).__name__}: {primary_error}"
                if primary_error
                else "primary skipped after repeated failures"
            )
            raise RuntimeError(
                f"{first}; backup {type(backup_error).__name__}: {backup_error}"
            ) from backup_error
        with self._lock:
            self.backup_calls += 1
        return response


def backup_calls(client: Any) -> int:
    """Calls the OpenRouter backup served through this client (0 for a plain client)."""
    return getattr(client, "backup_calls", 0)


def backup_line(calls: int) -> str | None:
    return f"Jev backup (OpenRouter) served {calls} calls" if calls else None


def backup_line_for_results(results: list) -> str | None:
    """Same line for extraction runs: counts the results whose details say `route: openrouter`."""
    return backup_line(sum(1 for x in results if (x.details or {}).get("route") == ROUTE))
