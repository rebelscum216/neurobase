"""The curation loop — folds raw captures into a small, current fact set.

Optimizes for deletion and supersession, not accumulation. Contract: spec appendix
§2 (plan/apply/consume/prune/synthesize; unconsumed-on-parse-failure is law).
"""

from __future__ import annotations

from neurobase.curator.engine import (
    curate,
    is_stale,
    node_name,
    read_fact_count_trend,
)

__all__ = ["curate", "is_stale", "node_name", "read_fact_count_trend"]
