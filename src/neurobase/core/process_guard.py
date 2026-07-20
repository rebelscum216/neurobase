"""Identify agent CLI processes launched internally by Neurobase."""

from __future__ import annotations

import os

INTERNAL_CALL_ENV = "NEUROBASE_INTERNAL_CALL"


def internal_call_env() -> dict[str, str]:
    """Return the current environment marked for a Neurobase-owned child."""
    env = os.environ.copy()
    env[INTERNAL_CALL_ENV] = "1"
    return env


def is_internal_call() -> bool:
    """Whether this process belongs to a Neurobase-owned agent CLI call."""
    return os.environ.get(INTERNAL_CALL_ENV) == "1"
