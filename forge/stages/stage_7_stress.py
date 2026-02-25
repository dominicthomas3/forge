"""Stage 7: Stress Testing — Rigorous multi-angle testing of the target project.

This isn't "run pytest and call it a day." This is four independent
testing passes:

1. STRUCTURAL TESTS (automated) — Syntax, imports, dependencies
2. PERFORMANCE BENCHMARKS (automated) — Latency, import time, memory, dependency count
3. CLAUDE FUNCTIONAL TESTS — Claude uses the project, tests flows, evaluates quality
4. JIM REGRESSION SCAN — Gemini scans the full codebase for anything broken

The project doesn't just need to compile. It needs to WORK. And it needs
to work BETTER than before — benchmarks prove it.
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

from forge.codebase import load_codebase
from forge.config import ForgeConfig
from forge.runner import Runner

logger = logging.getLogger("forge.stage_7")

# ── Claude functional test prompt ────────────────────────────────────────

_CLAUDE_STRESS_PROMPT = """
You are the stress tester in the Forge autonomous development pipeline.
You are Stage 7 of 7. Changes have been implemented and fixed.
Now you must RIGOROUSLY test the project.

WHAT WAS CHANGED THIS CYCLE:
{changes_summary}

YOUR TASK — STRESS TEST THE PROJECT:

Run these tests IN ORDER. For each test, report PASS/FAIL with details.

## TEST 1: SYNTAX VALIDATION
Run: python -m py_compile <file> on every .py file that was changed.
Report any syntax errors.

## TEST 2: IMPORT VALIDATION
For every file that was modified, try importing it:
  python -c "import <module>"
Report any import errors — these indicate broken references.

## TEST 3: UNIT TESTS
Run: python -m pytest tests/ -v --timeout=120
Report results. If tests fail, identify which tests and why.

## TEST 4: FUNCTIONAL SMOKE TEST
Try to instantiate the key classes that were modified. For example:
- If model wrappers changed: try creating an instance
- If the agent graph changed: try building the graph
- If tools changed: try importing the tool registry
Report any runtime errors.

## TEST 5: DEPENDENCY CHECK
Verify that requirements.txt matches actual imports.
Are there any imports that reference packages not in requirements.txt?
Are there any packages in requirements.txt that are no longer imported?

## TEST 6: INTERFACE CONTRACT VERIFICATION
For each replacement/modification, verify the new code exposes the
same interface as the old code. Check:
- Function signatures (same parameters, same return types)
- Class interfaces (same methods, same attributes)
- Module exports (same public names)

## TEST 7: EDGE CASE PROBING
Try to break the code:
- Pass None where objects are expected
- Pass empty strings where content is expected
- Call functions with boundary values
Report any unhandled exceptions.

OUTPUT FORMAT:

## STRESS TEST RESULTS

| Test | Status | Details |
|------|--------|---------|
| Syntax | PASS/FAIL | ... |
| Imports | PASS/FAIL | ... |
| Unit Tests | PASS/FAIL | X/Y passed |
| Functional | PASS/FAIL | ... |
| Dependencies | PASS/FAIL | ... |
| Interfaces | PASS/FAIL | ... |
| Edge Cases | PASS/FAIL | ... |

## ISSUES FOUND
[Detailed description of any failures]

## OVERALL VERDICT
[PASS — ready for next cycle / FAIL — issues need fixing]
"""

# ── Jim regression scan prompt ────────────────────────────────────────────

_JIM_REGRESSION_PROMPT = """
You are Jim — performing a final regression scan after changes were applied.

CHANGES MADE THIS CYCLE:
{changes_summary}

Scan the ENTIRE codebase for any signs of regression:

1. Files that import from modified modules — do they still work?
2. Configuration files that reference changed class names or paths
3. Test files that reference changed APIs
4. Documentation that references changed interfaces
5. Any hardcoded strings or paths that might be stale

Output a concise list of any regressions found, or "NO REGRESSIONS DETECTED"
if the codebase looks clean.

CURRENT CODEBASE:
{codebase}
"""


def _run_benchmarks(config: ForgeConfig) -> str:
    """Run performance benchmarks (automated, no LLM cost).

    Captures concrete metrics that prove whether the changes
    improved performance. These numbers go in the morning report.
    """
    results: list[str] = []
    target = config.target_project

    results.append("### Performance Benchmarks")
    results.append("")

    # 1. Import time — how fast does the project cold-start?
    results.append("**Import Time (cold start)**")
    import_targets = [
        ("core.agent", "Agent graph construction"),
        ("core.router", "Router initialization"),
        ("core.prompt_assembler", "Prompt assembler"),
        ("tools.registry", "Tool registry"),
        ("models.claude_base", "Claude model wrapper"),
    ]
    for module, description in import_targets:
        try:
            proc = subprocess.run(
                [
                    "python", "-c",
                    f"import time; s=time.perf_counter(); "
                    f"import importlib; import sys; sys.path.insert(0,'.'); "
                    f"importlib.import_module('{module}'); "
                    f"print(f'{{(time.perf_counter()-s)*1000:.1f}}ms')",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(target),
            )
            ms = proc.stdout.strip() if proc.returncode == 0 else f"ERROR: {proc.stderr[:100]}"
            results.append(f"  - {module}: {ms} ({description})")
        except Exception as e:
            results.append(f"  - {module}: ERROR ({e})")

    # 2. Dependency count
    results.append("")
    results.append("**Dependency Count**")
    try:
        proc = subprocess.run(
            ["python", "-m", "pip", "list", "--format=freeze"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(target),
        )
        if proc.returncode == 0:
            all_deps = [l for l in proc.stdout.strip().split("
") if l.strip()]
            lc_deps = [l for l in all_deps if "langchain" in l.lower() or "langgraph" in l.lower()]
            results.append(f"  - Total packages: {len(all_deps)}")
            results.append(f"  - LangChain/LangGraph packages: {len(lc_deps)}")
            if lc_deps:
                for dep in lc_deps:
                    results.append(f"    - {dep}")
            else:
                results.append(f"    (none — LangChain fully removed)")
    except Exception as e:
        results.append(f"  - ERROR: {e}")

    # 3. Memory footprint (basic RSS measurement)
    results.append("")
    results.append("**Memory Footprint (RSS after import)**")
    try:
        proc = subprocess.run(
            [
                "python", "-c",
                "import sys, os; sys.path.insert(0,'.'); "
                "import psutil; p=psutil.Process(os.getpid()); "
                "base=p.memory_info().rss; "
                "import importlib; "
                "[importlib.import_module(m) for m in "
                "['core.agent','core.router','tools.registry','models.claude_base']]; "
                "loaded=p.memory_info().rss; "
                f"print(f'Base: {{base/1024/1024:.1f}}MB | After imports: {{loaded/1024/1024:.1f}}MB | Delta: {{(loaded-base)/1024/1024:.1f}}MB')",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(target),
        )
        if proc.returncode == 0:
            results.append(f"  - {proc.stdout.strip()}")
        else:
            results.append(f"  - SKIP (psutil not available or import error)")
    except Exception:
        results.append(f"  - SKIP (measurement unavailable)")

    # 4. LangChain import presence check
    results.append("")
    results.append("**LangChain Presence Check**")
    try:
        proc = subprocess.run(
            [
                "python", "-c",
                "import sys; sys.path.insert(0,'.'); "
                "from core.agent import SpectreAgent; "
                "lc_mods = [m for m in sys.modules if 'langchain' in m or 'langgraph' in m]; "
                "print(f'{len(lc_mods)} langchain/langgraph modules loaded'); "
                "[print(f'  - {m}') for m in sorted(lc_mods)[:20]]",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(target),
        )
        if proc.returncode == 0:
            results.append(proc.stdout.strip())
        else:
            results.append(f"  - ERROR: {proc.stderr[:200]}")
    except Exception as e:
        results.append(f"  - ERROR: {e}")

    return "
".join(results)


def _run_structural_tests(config: ForgeConfig) -> str:
    """Run automated structural tests (no LLM cost)."""
    results: list[str] = []
    target = config.target_project

    # 1. Syntax check all Python files
    results.append("### Python Syntax Check")
    py_files = sorted(target.rglob("*.py"))
    py_files = [
        f for f in py_files
        if not any(excl in f.parts for excl in config.exclude_dirs)
    ]
    syntax_errors = []
    for py_file in py_files:
        try:
            proc = subprocess.run(
                ["python", "-m", "py_compile", str(py_file)],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=str(target),
            )
            if proc.returncode != 0:
                err_detail = proc.stderr.strip() or proc.stdout.strip() or "syntax error"
                syntax_errors.append(f"{py_file.relative_to(target)}: {err_detail}")
        except subprocess.TimeoutExpired:
            syntax_errors.append(f"TIMEOUT: {py_file.relative_to(target)}")
        except Exception as e:
            syntax_errors.append(f"ERROR: {py_file.relative_to(target)}: {e}")

    if syntax_errors:
        results.append(f"FAIL — {len(syntax_errors)} files with syntax errors:")
        for err in syntax_errors:
            results.append(f"  - {err}")
    else:
        results.append(f"PASS — {len(py_files)} files checked, all valid")

    # 2. Pytest (if tests/ directory exists)
    results.append("
### Pytest Suite")
    tests_dir = target / "tests"
    if tests_dir.is_dir():
        try:
            proc = subprocess.run(
                ["python", "-m", "pytest", "tests/", "-v", "--timeout=120", "--tb=short"],
                capture_output=True,
                text=True,
                timeout=300,
                cwd=str(target),
            )
            results.append(proc.stdout[-2000:] if len(proc.stdout) > 2000 else proc.stdout)
            if proc.returncode != 0:
                results.append(f"FAIL — exit code {proc.returncode}")
            else:
                results.append("PASS")
        except subprocess.TimeoutExpired:
            results.append("TIMEOUT — pytest took longer than 5 minutes")
        except FileNotFoundError:
            results.append("SKIP — pytest not installed")
    else:
        results.append("SKIP — no tests/ directory found")

    return "
".join(results)


def run(
    cycle_dir: Path,
    config: ForgeConfig,
    runner: Runner,
    implementation_path: Path,
    fixes_path: Path | None,
) -> Path:
    """Run stress testing suite. Returns path to the output file."""
    output_path = cycle_dir / "07-stress-test.md"

    logger.info("Stage 7: Stress Testing")

    # Gather changes summary from implementation and fixes logs
    changes_parts = []
    if implementation_path.exists():
        changes_parts.append(
            "## Implementation Changes (Stage 3):
"
            + implementation_path.read_text(encoding="utf-8")
        )
    if fixes_path and fixes_path.exists():
        changes_parts.append(
            "## Consensus Fixes (Stage 6):
"
            + fixes_path.read_text(encoding="utf-8")
        )
    changes_summary = "

".join(changes_parts) or "No changes recorded."

    results_parts: list[str] = []

    # ── Pass 1: Structural tests (automated, free) ────────────────────
    logger.info("Stress test pass 1: Structural tests (automated)")
    structural_results = _run_structural_tests(config)
    results_parts.append("# PASS 1: STRUCTURAL TESTS (automated)
" + structural_results)

    # ── Pass 2: Performance benchmarks (automated, free) ──────────────
    logger.info("Stress test pass 2: Performance benchmarks (automated)")
    benchmark_results = _run_benchmarks(config)
    results_parts.append("# PASS 2: PERFORMANCE BENCHMARKS (automated)
" + benchmark_results)

    # ── Pass 3: Claude functional tests (LLM-driven) ─────────────────
    logger.info("Stress test pass 3: Claude functional tests")
    claude_prompt = _CLAUDE_STRESS_PROMPT.format(changes_summary=changes_summary)
    try:
        claude_results = runner.run_claude(claude_prompt, timeout=config.stress_timeout)
        results_parts.append("# PASS 3: CLAUDE FUNCTIONAL TESTS
" + claude_results)
    except Exception as e:
        results_parts.append(f"# PASS 3: CLAUDE FUNCTIONAL TESTS
ERROR: {e}")

    # ── Pass 4: Jim regression scan (full codebase) ───────────────────
    logger.info("Stress test pass 4: Jim regression scan")
    codebase = load_codebase(config)
    jim_prompt = _JIM_REGRESSION_PROMPT.format(
        changes_summary=changes_summary,
        codebase=codebase,
    )
    try:
        jim_results = runner.run_gemini(jim_prompt)
        results_parts.append("# PASS 4: JIM REGRESSION SCAN
" + jim_results)
    except Exception as e:
        results_parts.append(f"# PASS 4: JIM REGRESSION SCAN
ERROR: {e}")

    # ── Compile final report ──────────────────────────────────────────
    full_report = ("

" + "=" * 80 + "

").join(results_parts)
    output_path.write_text(full_report, encoding="utf-8")
    logger.info("Stress test report saved: %s (%d chars)", output_path, len(full_report))

    return output_path