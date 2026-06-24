"""
Isolated, per-competitor benchmark harness for turbohtml.

Each competitor is timed in its own uv venv holding only that one library; turbohtml's own baseline is timed once in a
turbohtml-only venv and shared across every comparison. The orchestrator (:mod:`bench.orchestrator`) provisions the
venvs and renders the tables; the shared inputs (:mod:`bench.cases`) and renderers (:mod:`bench.report`) depend on
nothing beyond the standard library, so no module ever imports more than the single competitor it benchmarks.
"""

from __future__ import annotations
