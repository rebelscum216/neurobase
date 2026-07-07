"""The curation loop — folds raw captures into a small, current fact set.

Optimizes for deletion and supersession, not accumulation. Contract: spec appendix
§2 (plan/apply/consume/prune/synthesize; unconsumed-on-parse-failure is law).
Implemented in Phase 3.
"""
