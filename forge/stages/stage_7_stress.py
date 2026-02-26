"""Stage 7: Stress Testing — Rigorous multi-angle testing of the target project.

This isn't "run pytest and call it a day." This is five independent
testing passes:

1. STRUCTURAL TESTS (automated) — Syntax, imports, dependencies
2. PERFORMANCE BENCHMARKS (automated) — Latency, import time, memory, dependency count
3. CLAUDE FUNCTIONAL TESTS — Claude uses the project, tests flows, evaluates quality
4. JIM REGRESSION SCAN — Gemini scans the full codebase for anything broken
5. TOKEN AUDIT (automated) — Validates caching works, detects token bloat, verifies
   prompt efficiency. This is critical: if the middleware replacement silently drops
   cache_control headers or inflates prompts, this pass catches it before convergence.

The project doesn't just need to compile. It needs to WORK. And it needs
to work BETTER than before — benchmarks prove it. And it must NOT regress
on token efficiency.
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


_CLAUDE_TOKEN_AUDIT_PROMPT = """
You are the Token Efficiency Auditor in the Forge pipeline. Your job is to
verify that the middleware replacement has NOT introduced token bloat, cache
leaks, or prompt inflation.

This is CRITICAL. The owner spent a week fixing 3x token overconsumption.
Any regression here is an automatic FAIL.

CHANGES MADE THIS CYCLE:
{changes_summary}

YOUR TASK — AUDIT TOKEN EFFICIENCY:

## AUDIT 1: CACHE HEADER VERIFICATION
Inspect every file in models/ that makes API calls. Verify:
- Anthropic calls include `cache_control` blocks on system messages
- The cache_control format matches Anthropic's spec: {{"type": "ephemeral"}}
- Tool definitions include cache_control on the last tool (for tool caching)
- If ANY cache_control logic was removed or broken, this is a FAIL.

Report: Which files have cache_control? Is it correctly formatted? Any gaps?

## AUDIT 2: PROMPT ASSEMBLY BLOAT CHECK
Read core/prompt_assembler.py. Verify:
- Token budget is still enforced (16k for Opus/Sonnet/Pro, 3k for Flash)
- Priority ordering is preserved (personality > facts > tools > summary > window > recall)
- No new system instructions or hidden text was injected by the replacement
- The assembled prompt is NOT larger than before the changes

Report: What is the token budget? Are priorities intact? Any new text injected?

## AUDIT 3: MESSAGE FORMAT EFFICIENCY
Check how messages are built before API calls. Verify:
- Messages are minimal — no extra metadata, no framework wrappers left behind
- Tool results are sent as concise strings, not bloated objects
- No duplicate messages in the conversation history
- System prompt is sent ONCE, not repeated per message

Report: Are messages lean? Any redundancy? Any wrapper artifacts?

## AUDIT 4: FLASH DAEMON SCOPE CHECK
Gemini Flash handles cheap background work (memory summarization, context
compilation, journal entries). Verify:
- Flash is NEVER used for user-facing conversations
- Flash calls have max_output_tokens capped (should be ~80)
- Flash is not accidentally getting full prompt assemblies meant for Opus/Sonnet

Report: Is Flash scoped correctly? Any scope creep?

## AUDIT 5: TOKEN TRACKING INTEGRITY
Check models/token_tracker.py and cost calculation in model adapters. Verify:
- Input tokens, output tokens, cached tokens are all tracked correctly
- Cost calculation uses the right pricing per model
- cache_read_input_tokens and cache_creation_input_tokens are captured (Anthropic)
- No token counts are being silently dropped or zeroed

Report: Is tracking complete? Any missing metrics?

## AUDIT 6: UNNECESSARY LLM CALLS
Scan the full pipeline flow. Check for:
- Any place where an LLM is called when a local operation would suffice
- Router using an LLM call (should be pure keyword matching, zero tokens)
- Memory operations making redundant LLM calls
- Any retry logic that resubmits the full prompt on transient errors (should retry the same call, not rebuild)

Report: Any unnecessary LLM invocations found?

OUTPUT FORMAT:

## TOKEN AUDIT RESULTS

| Audit | Status | Details |
|-------|--------|---------|
| Cache Headers | PASS/FAIL | ... |
| Prompt Bloat | PASS/FAIL | ... |
| Message Efficiency | PASS/FAIL | ... |
| Flash Scope | PASS/FAIL | ... |
| Token Tracking | PASS/FAIL | ... |
| Unnecessary Calls | PASS/FAIL | ... |

## TOKEN EFFICIENCY VERDICT
[PASS — token efficiency preserved or improved / FAIL — token regression detected]

## ISSUES FOUND
[Detailed description of any token efficiency problems]
"""


def _run_token_audit(config: ForgeConfig) -> str:
    """Run automated token audit checks (no LLM cost).

    Validates that caching infrastructure, prompt budgets, and token
    tracking are intact after middleware changes. These are the checks
    that catch silent cache leaks and prompt bloat.
    """
    results: list[str] = []
    target = config.target_project

    results.append("### Automated Token Audit Checks")
    results.append("")

    # 1. Cache control presence in model adapters
    results.append("**Cache Control Presence**")
    cache_files_checked = 0
    cache_issues = []

    for model_file in sorted((target / "models").glob("*.py")):
        if model_file.name.startswith("__"):
            continue
        cache_files_checked += 1
        content = model_file.read_text(encoding="utf-8", errors="ignore")

        # Check for cache_control in Claude adapters
        if "claude" in model_file.name.lower() or "anthropic" in content.lower():
            if "cache_control" not in content:
                cache_issues.append(
                    f"  - {model_file.name}: Claude adapter MISSING cache_control"
                )
            else:
                results.append(f"  - {model_file.name}: cache_control PRESENT")

    if cache_issues:
        results.extend(cache_issues)
        results.append("  *** FAIL: Cache control missing from Claude adapters ***")
    else:
        results.append(f"  - Checked {cache_files_checked} model files — all Claude adapters have cache_control")

    # 2. Prompt budget enforcement
    results.append("")
    results.append("**Prompt Token Budget**")
    assembler_path = target / "core" / "prompt_assembler.py"
    if assembler_path.exists():
        asm_content = assembler_path.read_text(encoding="utf-8", errors="ignore")
        # Look for budget constants
        budget_found = False
        for marker in ["16000", "16_000", "token_budget", "max_tokens", "budget"]:
            if marker in asm_content.lower():
                budget_found = True
                break
        if budget_found:
            results.append("  - Token budget enforcement: PRESENT")
        else:
            results.append("  - Token budget enforcement: NOT FOUND — possible bloat risk")

        # Check priority ordering
        if "priority" in asm_content.lower() or "personality" in asm_content.lower():
            results.append("  - Priority-based assembly: PRESENT")
        else:
            results.append("  - Priority-based assembly: NOT FOUND — check prompt_assembler")
    else:
        results.append("  - WARNING: prompt_assembler.py not found")

    # 3. Flash scope check
    results.append("")
    results.append("**Gemini Flash Scope Check**")
    flash_issues = []
    for flash_file in (target / "models").glob("*flash*"):
        content = flash_file.read_text(encoding="utf-8", errors="ignore")
        # Check max output tokens is capped low
        if "max_output_tokens" in content:
            import re
            matches = re.findall(r"max_output_tokens\s*[=:]\s*(\d+)", content)
            for val in matches:
                if int(val) > 200:
                    flash_issues.append(
                        f"  - {flash_file.name}: max_output_tokens={val} (should be <=200 for daemon)"
                    )
                else:
                    results.append(f"  - {flash_file.name}: max_output_tokens={val} — correctly capped")
        # Check tools are disabled
        if "tools" in content.lower():
            # Check if tools are explicitly disabled or absent
            if "no tools" in content.lower() or "tools=none" in content.lower() or "tools=[]" in content.lower():
                results.append(f"  - {flash_file.name}: tools correctly disabled")
            elif "get_tools" in content and "[]" not in content:
                flash_issues.append(
                    f"  - {flash_file.name}: may have tools enabled (should be daemon-only, no tools)"
                )

    if flash_issues:
        results.extend(flash_issues)
    elif not list((target / "models").glob("*flash*")):
        results.append("  - No flash model file found")

    # 4. Token tracker completeness
    results.append("")
    results.append("**Token Tracker Completeness**")
    tracker_path = target / "models" / "token_tracker.py"
    if tracker_path.exists():
        tracker_content = tracker_path.read_text(encoding="utf-8", errors="ignore")
        tracked_metrics = []
        for metric in ["input_tokens", "output_tokens", "cached_tokens", "cache_read", "cache_creation", "cost"]:
            if metric in tracker_content:
                tracked_metrics.append(metric)
        results.append(f"  - Tracked metrics: {', '.join(tracked_metrics)}")
        if "input_tokens" in tracked_metrics and "output_tokens" in tracked_metrics:
            results.append("  - Core token tracking: PRESENT")
        else:
            results.append("  - Core token tracking: INCOMPLETE — missing input/output counts")
        if "cached_tokens" in tracked_metrics or "cache_read" in tracked_metrics:
            results.append("  - Cache metric tracking: PRESENT")
        else:
            results.append("  - Cache metric tracking: NOT FOUND — cache effectiveness invisible")
    else:
        results.append("  - WARNING: token_tracker.py not found")

    # 5. No LLM in router check
    results.append("")
    results.append("**Router LLM-Free Check**")
    router_path = target / "core" / "router.py"
    if router_path.exists():
        router_content = router_path.read_text(encoding="utf-8", errors="ignore")
        llm_markers = ["invoke", "generate", "astream", "completion", "chat("]
        router_llm_calls = [m for m in llm_markers if m in router_content]
        if router_llm_calls:
            results.append(f"  - WARNING: Router contains LLM-like calls: {router_llm_calls}")
            results.append("  - Router should be pure keyword matching (zero tokens)")
        else:
            results.append("  - Router is LLM-free: PASS (pure keyword matching)")

    # 6. LangChain message wrapper check (should be gone after replacement)
    results.append("")
    results.append("**Message Wrapper Bloat Check**")
    bloat_markers = ["HumanMessage", "AIMessage", "SystemMessage", "ToolMessage"]
    files_with_wrappers = []
    for py_file in sorted(target.rglob("*.py")):
        if any(excl in py_file.parts for excl in ("__pycache__", ".venv", "node_modules", "tests")):
            continue
        try:
            content = py_file.read_text(encoding="utf-8", errors="ignore")
            found = [m for m in bloat_markers if m in content]
            if found:
                files_with_wrappers.append((py_file.relative_to(target), found))
        except Exception:
            pass

    if files_with_wrappers:
        results.append(f"  - {len(files_with_wrappers)} files still using LangChain message wrappers:")
        for fpath, markers in files_with_wrappers[:10]:
            results.append(f"    - {fpath}: {', '.join(markers)}")
        results.append("  - NOTE: These add serialization overhead per message")
    else:
        results.append("  - No LangChain message wrappers found — messages are lean")

    return "\\n".join(results)


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
            all_deps = [l for l in proc.stdout.strip().split("\\n") if l.strip()]
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

    return "\\n".join(results)


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
            # Use in-memory compile() instead of py_compile to avoid
            # __pycache__ disk writes. On Windows, sequential py_compile
            # subprocesses deadlock when Defender locks __pycache__ between
            # writes. compile() validates syntax identically (py_compile
            # calls it internally) without any disk I/O.
            proc = subprocess.run(
                [
                    "python", "-c",
                    "import tokenize,sys;f=tokenize.open(sys.argv[1]);"
                    "compile(f.read(),sys.argv[1],'exec')",
                    str(py_file),
                ],
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
    results.append("\\n### Pytest Suite")
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

    return "\\n".join(results)


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
            "## Implementation Changes (Stage 3):\\n"
            + implementation_path.read_text(encoding="utf-8")
        )
    if fixes_path and fixes_path.exists():
        changes_parts.append(
            "## Consensus Fixes (Stage 6):\\n"
            + fixes_path.read_text(encoding="utf-8")
        )
    changes_summary = "\\n\\n".join(changes_parts) or "No changes recorded."

    results_parts: list[str] = []

    # ── Pass 1: Structural tests (automated, free) ────────────────────
    logger.info("Stress test pass 1: Structural tests (automated)")
    structural_results = _run_structural_tests(config)
    results_parts.append("# PASS 1: STRUCTURAL TESTS (automated)\\n" + structural_results)

    # ── Pass 2: Performance benchmarks (automated, free) ──────────────
    logger.info("Stress test pass 2: Performance benchmarks (automated)")
    benchmark_results = _run_benchmarks(config)
    results_parts.append("# PASS 2: PERFORMANCE BENCHMARKS (automated)\\n" + benchmark_results)

    # ── Pass 3: Claude functional tests (LLM-driven) ─────────────────
    logger.info("Stress test pass 3: Claude functional tests")
    claude_prompt = _CLAUDE_STRESS_PROMPT.format(changes_summary=changes_summary)
    try:
        claude_results = runner.run_claude(claude_prompt, timeout=config.stress_timeout, needs_filesystem=False)
        results_parts.append("# PASS 3: CLAUDE FUNCTIONAL TESTS\\n" + claude_results)
    except Exception as e:
        results_parts.append(f"# PASS 3: CLAUDE FUNCTIONAL TESTS\\nERROR: {e}")

    # ── Pass 4: Jim regression scan (full codebase) ───────────────────
    logger.info("Stress test pass 4: Jim regression scan")
    codebase = load_codebase(config)
    jim_prompt = _JIM_REGRESSION_PROMPT.format(
        changes_summary=changes_summary,
        codebase=codebase,
    )
    try:
        jim_results = runner.run_gemini(jim_prompt)
        results_parts.append("# PASS 4: JIM REGRESSION SCAN\\n" + jim_results)
    except Exception as e:
        results_parts.append(f"# PASS 4: JIM REGRESSION SCAN\\nERROR: {e}")

    # ── Pass 5: Token audit (automated + LLM-driven) ────────────────
    logger.info("Stress test pass 5: Token efficiency audit")

    # 5a. Automated token checks (free, no LLM cost)
    automated_token_results = _run_token_audit(config)

    # 5b. Claude deep token audit (LLM-driven, inspects actual code logic)
    claude_token_prompt = _CLAUDE_TOKEN_AUDIT_PROMPT.format(
        changes_summary=changes_summary,
    )
    try:
        claude_token_results = runner.run_claude(
            claude_token_prompt, timeout=config.stress_timeout, needs_filesystem=False
        )
    except Exception as e:
        claude_token_results = f"ERROR: {e}"

    token_audit_report = (
        automated_token_results
        + "\\n\\n---\\n\\n### Claude Token Audit (LLM-driven)\\n"
        + claude_token_results
    )
    results_parts.append("# PASS 5: TOKEN EFFICIENCY AUDIT\\n" + token_audit_report)

    # ── Compile final report ──────────────────────────────────────────
    full_report = ("\\n\\n" + "=" * 80 + "\\n\\n").join(results_parts)
    output_path.write_text(full_report, encoding="utf-8")
    logger.info("Stress test report saved: %s (%d chars)", output_path, len(full_report))

    return output_path