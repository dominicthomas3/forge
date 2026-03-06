"""Forge — Multi-model autonomous development pipeline.

Chains Gemini 3.1 Pro (Jim) → Deep Think → Claude → Verify → Loop.
Runs overnight on subscriptions (Claude Max + Google Pro Ultra). $0 marginal cost.

Usage:
    python -m forge.run_overnight --task "Replace LangChain with direct SDK calls"
    python -m forge.run_overnight --task-file my_task.md
"""

__version__ = "0.0.2"