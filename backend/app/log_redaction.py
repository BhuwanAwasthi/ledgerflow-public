"""Keep URL tokens out of the logs: query strings (`?t=` stream tokens, `?ft=` file tokens), vendor-portal
tokens in `/portal/<token>` paths and bearer tokens are replaced before a record is written.

Installed on the root handlers and on uvicorn's access logger (whose request line includes the query string).
"""

from __future__ import annotations

import logging
import re

_QUERY = re.compile(r"(/[^\s?\"']*)\?[^\s\"']*")
_PORTAL = re.compile(r"(/portal/)[^/\s?\"']+")
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
REDACTED = "[redacted]"


def redact(text: str) -> str:
    text = _QUERY.sub(r"\1", text)
    text = _PORTAL.sub(rf"\g<1>{REDACTED}", text)
    return _BEARER.sub(rf"\g<1>{REDACTED}", text)


class RedactTokens(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and record.args:
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: redact(v) if isinstance(v, str) else v for k, v in record.args.items()}
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        return True


_FILTER = RedactTokens()


def install() -> None:
    """Idempotent: attach the filter to the root handlers and the uvicorn loggers."""
    targets: list[logging.Filterer] = [*logging.getLogger().handlers]
    for name in ("uvicorn.access", "uvicorn.error", "uvicorn"):
        lg = logging.getLogger(name)
        targets += [lg, *lg.handlers]
    for t in targets:
        if _FILTER not in t.filters:
            t.addFilter(_FILTER)
