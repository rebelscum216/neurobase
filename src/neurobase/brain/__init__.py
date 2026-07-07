"""Execution-backend abstraction for LLM steps (decision D9).

Precedence claude-cli → codex-cli → anthropic-api → openai-api; ollama is a
documented seam. Contract in spec appendix §11.3 (the `claude -p` envelope).
Implemented in Phase 2.
"""
