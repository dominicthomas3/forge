# Forge — Project-Level Agent Instructions

You are working inside the **Forge** project at `C:\Users\domin\forge`.

## Quick Reference

- **Pipeline code:** `forge/orchestrator.py` (7-stage convergence loop)
- **Stage implementations:** `forge/stages/stage_1_jim.py` through `stage_7_stress.py`
- **Runner (model execution):** `forge/runner.py`
- **Config:** `forge/config.py`
- **Task directive:** `task_proprietary_middleware.md` — THE mission spec. Read this.
- **Handoff:** `HANDOFF.md` — full context from previous sessions. Read this at session start.
- **Runtime data:** `forge/.forge_data/` — cycle outputs, reports, logs

## Current Mission

Replace Spectre's LangChain/LangGraph middleware with a 100% proprietary, zero-dependency engine. See `task_proprietary_middleware.md` for the full directive.

## Target Codebase

Spectre lives at `C:\Users\domin\spectre`. The Forge pipeline targets it via `--target C:\Users\domin\spectre`.

## Running the Pipeline

```bash
# Full run
python -m forge.run_overnight --task "Replace all LangChain dependencies with direct SDK calls" --target C:\Users\domin\spectre --max-hours 8

# Dry run (validate config only)
python -m forge.run_overnight --task "..." --dry-run

# Morpheus (Spectre's sparring partner)
python -m forge.run_morpheus --target C:\Users\domin\spectre --exchanges 5
```

## When Things Break

Read the logs in `forge/.forge_data/`. Check `git log` and `git status`. Troubleshoot the root cause — do NOT just restart.
