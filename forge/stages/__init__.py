"""Forge pipeline stages.

Stage 1: Jim Analysis      — Gemini 3.1 Pro loads full codebase, produces analysis + plan
Stage 2: Deep Think Verify  — Claude crafts prompt → Deep Think stress-tests Jim's plan
Stage 3: Claude Implement   — Claude Code applies Deep Think's recommendations
Stage 4: Claude Review      — Claude self-reviews changes (finds errors, does NOT fix)
Stage 5: Consensus          — Jim + Claude must agree on what to fix
Stage 6: Apply Fixes        — Claude applies only the agreed-upon fixes
Stage 7: Stress Test        — Rigorous multi-angle testing of the target project
"""