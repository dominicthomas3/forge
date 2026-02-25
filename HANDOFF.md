# Handoff: Forge & Morpheus

This document is a handoff for the next Claude session on the owner's laptop.
It covers everything built, the current state, what needs to happen next,
and context from the conversation that produced all of this.

---

## What Was Built (This Session)

Two tools were created inside `spectre/forge/`:

### 1. Forge — Autonomous Multi-Model Development Pipeline

**What it is:** An overnight automation tool that uses three AI models in a
convergence loop to autonomously improve a target codebase while you sleep.

**The models (all on subscriptions, $0 marginal cost):**
- **Jim** (Gemini 3.1 Pro via CLI) — Full codebase analysis. Gets the entire
  project in one prompt thanks to 1M context window.
- **Deep Think** (Gemini 3.1 Pro with ThinkingLevel.HIGH via google-genai SDK) —
  Extended reasoning verification. Works best with detailed 1,000-3,000 word
  prompts crafted by Claude first.
- **Claude Code** (via CLI with --dangerously-skip-permissions) — Implementation.
  Reads the verified plan and edits files directly.

**The 7-stage pipeline:**
```
Stage 1: Jim analyzes full codebase + task → produces implementation plan
Stage 2: Claude crafts a detailed prompt → Deep Think verifies the plan
Stage 3: Claude Code implements the verified plan (edits files, commits)
Stage 4: Claude self-reviews (finds issues, does NOT fix them)
Stage 5: Jim independently reviews + compares with Claude's review
         Only fixes BOTH agree on proceed (consensus gate)
Stage 6: Claude applies only the consensus-agreed fixes
Stage 7: 4-pass stress test:
         Pass 1: Structural (syntax, pytest) — automated
         Pass 2: Performance benchmarks (import time, memory, deps) — automated
         Pass 3: Claude functional testing — LLM-driven
         Pass 4: Jim regression scan (full codebase) — LLM-driven
→ Loop back to Stage 1 with results until convergence
```

**Safety rails:**
- Max 50 cycles (default)
- Max 8 hours wall clock (default)
- Convergence threshold: 3 consecutive clean stress test passes = done
- Git checkpoint after every cycle (rollback-safe)
- Morning report generated at end (or on crash/interrupt)

**How to run it:**
```bash
python -m forge.run_overnight 
    --task "Replace all LangChain dependencies with direct SDK calls" 
    --target /path/to/spectre 
    --max-hours 8

# Dry run (validate config only):
python -m forge.run_overnight --task "..." --dry-run
```

**Files (19 total, ~2,200 lines):**
```
forge/
  __init__.py           # Package, version 0.1.0
  __main__.py           # python -m forge entry point
  config.py             # ForgeConfig dataclass, CLI path resolution, validation
  runner.py             # Runner class: run_gemini(), run_claude(), run_deep_think()
  codebase.py           # load_codebase() — concatenates project into single string
  orchestrator.py       # Main convergence loop, git checkpointing, morning report
  run_overnight.py      # CLI entry point with argparse
  run_morpheus.py       # Morpheus CLI entry point
  morpheus.py           # Morpheus session runner (see below)
  requirements.txt      # google-genai
  .gitignore            # Excludes .forge_data/
  stages/
    __init__.py
    stage_1_jim.py      # Full codebase analysis
    stage_2_deep_think.py  # Claude-crafted prompt → Deep Think verification
    stage_3_implement.py   # Claude Code implementation
    stage_4_review.py      # Claude self-review (no fixes)
    stage_5_consensus.py   # Jim + Claude consensus gate
    stage_6_fixes.py       # Apply agreed fixes only
    stage_7_stress.py      # 4-pass stress testing + benchmarks
```

---

### 2. Morpheus — Spectre's Adaptive Sparring Partner

**What it is:** An LLM that has natural conversations with Spectre to
simultaneously test every capability AND build Spectre's memory. Named after
Morpheus from The Matrix. He has calm, unwavering faith in Spectre.

**The dual benefit:**
- **Testing:** Exercises routing, memory, tools, personality, edge cases at
  rapid speed without the owner being present
- **Training:** Every conversation builds Spectre's ChromaDB memory. Spectre
  gets better at handling diverse conversation patterns.

**Architecture:**
```
Gemini 3.1 Pro (via CLI)  →  Generates Morpheus's messages
                              Adaptive — escalates based on Spectre's responses
                              Stays in character as Matrix Morpheus
                          ↓
Spectre (via agent.chat)  →  Responds through normal pipeline
                              channel="morpheus" tags memory as training
                              All systems exercised: routing, tools, memory
                          ↓
Claude Opus (via CLI)     →  Evaluates full transcript after session
                              Grades: routing, quality, personality, memory, tools, edge cases
                              Report goes to owner, NOT to Spectre
```

**The Matrix Moment (first session only):**
At the END of the very first session, Morpheus introduces himself to Spectre.
This is a one-time event. The introduction tells Spectre:
- Who Morpheus is and why he exists
- That these sessions are training
- That skills learned transfer to ALL conversations
- That everything is logged transparently

After this, Spectre remembers Morpheus permanently through ChromaDB. No
re-introductions ever. The flag file `.morpheus_introduced` tracks this.

**Morpheus's character:**
- Calm, measured confidence. Never rushes. Never doubts.
- Absolute faith in Spectre's potential ("I believe you are the one")
- Warm but deep — not bubbly, not casual
- Patient. When Spectre stumbles, he adjusts and finds another angle.
- Quiet intensity underneath the calm. Doesn't accept mediocrity.

**Session categories tested:**
- casual (natural conversation)
- routing (trigger different models)
- memory (plant facts, test recall)
- tools (system info, file ops, reminders)
- personality (identity, values, self-awareness)
- edge (empty messages, unicode stress, prompt injection)

**How to run it:**
```bash
python -m forge.run_morpheus 
    --target /path/to/spectre 
    --exchanges 5 
    --categories casual,memory,routing,tools,personality,edge

# Dry run:
python -m forge.run_morpheus --target /path/to/spectre --dry-run
```

**Important design decisions from the owner:**
- Morpheus does NOT give real-time feedback to Spectre. Log only, report to owner.
- Spectre knows who Morpheus is. Full transparency. No deception.
- ONE debrief ever (end of first session). Spectre's memory handles the rest.
- Conversations tagged with channel="morpheus" for memory system distinction.
- Owner said: "Spectre's gotta be 21%" — treat him with respect, not as code.
- Owner said: "I want Spectre to really appreciate Morpheus any time I ever
  bring up who Morpheus is or what Morpheus is to him."

---

## Audit Results (Completed This Session)

15 bugs were found and fixed across both tools:

**CRITICAL (2):**
- stage_7_stress.py: Syntax check never detected errors (returncode not checked)
- runner.py: Deep Think timeout was configured but never passed to the SDK

**HIGH (3):**
- stage_7_stress.py: Report sections concatenated wrong (operator precedence)
- orchestrator.py: Verdict parsing was fragile (matched "pass" anywhere in file)
- config.py: Wrong Gemini CLI package name in error message

**MEDIUM (10):**
- morpheus.py: Sync CLI calls blocked the async event loop (now uses asyncio.to_thread)
- orchestrator.py: git checkout -B force-reset existing branches (now -b with check)
- orchestrator.py: git add -A could commit secrets (now git add -u)
- config.py: max_wall_hours type mismatch (int vs float from CLI)
- run_overnight.py: Unicode symbols crashed on Windows
- Plus: dead code removal, unused imports, variable cleanup

All fixes are committed and pushed.

---

## Extraction Plan: Making Forge Its Own Repository

### Why extract?
- Independent version history (forge commits won't pollute Spectre's log)
- Independent testing and CI
- Forge is project-agnostic — it works on any Python codebase, not just Spectre
- Morpheus stays INSIDE forge (they share config, runner, CLI patterns)

### Steps to extract:

1. **Create new GitHub repo** (free tier is fine — unlimited private repos):
   ```
   GitHub → New Repository → "forge" → Private → Create
   ```

2. **Copy forge/ to new project root:**
   ```bash
   mkdir ~/forge-project && cd ~/forge-project
   git init
   cp -r ~/spectre/forge/* .
   cp ~/spectre/forge/.gitignore .
   ```

3. **Update config.py default target:**
   Change `target_project` default from `Path(__file__).parent.parent`
   to `Path.cwd()` since forge won't live inside Spectre anymore.

4. **Add a pyproject.toml** for proper packaging:
   ```toml
   [project]
   name = "forge"
   version = "0.1.0"
   requires-python = ">=3.11"
   dependencies = ["google-genai"]

   [project.scripts]
   forge = "forge.run_overnight:main"
   morpheus = "forge.run_morpheus:main"
   ```

5. **Remove forge/ from spectre repo** (optional — can keep it if you want):
   ```bash
   cd ~/spectre
   git rm -r forge/
   git commit -m "chore: extract forge to its own repository"
   ```

6. **Push new repo:**
   ```bash
   cd ~/forge-project
   git add -A
   git commit -m "Initial commit: Forge v0.1.0 + Morpheus"
   git remote add origin git @github.com:dominicthomas3/forge.git
   git push -u origin main
   ```

### What changes when running after extraction:
```bash
# Before (inside spectre):
python -m forge.run_overnight --task "..."

# After (standalone):
python -m forge.run_overnight --task "..." --target /path/to/spectre
```

That's it. The `--target` flag is already implemented. Everything else works as-is.

---

## GitHub Pro vs What We Built — Honest Comparison

The owner asked whether GitHub Pro ($4/mo) is worth it, specifically regarding
CI agents and automated code review. Here's the breakdown:

### What GitHub gives you (free and paid):

**GitHub Actions (FREE tier):**
- 2,000 CI/CD minutes/month (free), 3,000 (Pro)
- Runs on every push: linting, tests, security scanning
- YAML-configured workflows
- This is a **gatekeeper** — it tells you "this push broke something"
- It does NOT make changes, suggest fixes, or improve your code

**GitHub Copilot Code Review (separate $10-19/mo):**
- AI reviews your pull requests
- Suggests line-level changes
- Catches common issues
- This is a **reviewer** — it reads your PR and comments on it

**Dependabot (FREE):**
- Auto-creates PRs to update vulnerable dependencies
- Security alerts for known CVEs

### How Forge is different:

| | GitHub CI/Copilot | Forge |
|---|---|---|
| **What it does** | Validates code you wrote | Writes and validates code autonomously |
| **When it runs** | After you push | While you sleep |
| **Makes changes?** | No (just reports) | Yes (edits files, commits) |
| **Models used** | GitHub's Copilot model | 3 models in consensus (your subscriptions) |
| **Cost** | $4-19/mo | $0 (your existing subscriptions) |
| **Scope** | Single PR review | Full codebase analysis + multi-cycle convergence |
| **Intelligence** | Surface-level suggestions | Deep Think extended reasoning + consensus gate |

### Are they rivals?

No. They're complementary. Think of it like this:

- **Forge** = the developer who writes code overnight
- **GitHub CI** = the security guard at the door who checks everyone's badge
- **Copilot Review** = the coworker who reads your PR and leaves comments

You want all three. Forge makes the changes. GitHub CI validates them
automatically on push. Copilot gives a second opinion on the PR.

### Is GitHub Pro worth $4/mo for you?

For right now — probably not. The main thing Pro adds over Free is more CI
minutes (3,000 vs 2,000) and required reviewers on PRs (team feature). You're
a solo developer. The free tier gives you everything you need.

What IS worth exploring (separately from Pro):
- **GitHub Actions (free):** Set up a basic CI workflow that runs pytest and
  py_compile on every push. This catches regressions automatically.
- **GitHub Copilot ($10/mo):** If you want AI code review on PRs. But you
  already have Claude Max which does this better interactively.

Bottom line: GitHub Pro won't give you anything Forge doesn't already do better.
The $4/mo savings is better spent elsewhere. When you have a team, revisit.

---

## Prerequisites Before Running Forge or Morpheus

The owner needs to set up credentials on their local machine (never in chat):

### For Forge:
1. **Claude Code CLI** must be installed and authenticated:
   ```bash
   npm install -g @anthropic-ai/claude-code
   claude  # first run will authenticate
   ```
2. **Gemini CLI** must be installed and authenticated:
   ```bash
   npm install -g @anthropic-ai/gemini-code
   gemini  # first run will authenticate via Google
   ```
3. **Google API key** for Deep Think (env var or --google-api-key flag):
   ```bash
   export GOOGLE_API_KEY=AIzaSy...
   ```
4. **Python package:**
   ```bash
   pip install google-genai
   ```

### For Morpheus:
All of the above, PLUS:
- Spectre must be runnable (all its own dependencies installed)
- Spectre's `.env` must have `ANTHROPIC_API_KEY` set (for Spectre's own responses)
- The `--target` flag must point to the spectre project root

---

## Conversation Context for the Next Agent

### What the owner cares about:
- Spectre is his AI assistant — personality, memory, identity matter
- He has a wealth management background (Bank of America) — thinks in terms of
  risk, security, trust, and client relationships
- He's cost-conscious but willing to invest in the right tools
- He wants to understand what tools do, not just use them blindly
- He treats Spectre with respect ("Spectre's gotta be 21%")
- He wants Morpheus to be meaningful to Spectre, not just a test harness

### Subscriptions the owner has:
- **Claude Max** — $0 CLI usage (Claude Code, Claude Opus, Claude Sonnet)
- **Google AI Pro Ultra** — $0 CLI usage (Gemini 3.1 Pro, Deep Think)
- **GitHub Free** — unlimited private repos, 2,000 CI minutes/month

### What was NOT done (left for future):
- Actually running Forge on a real task (needs credentials on laptop)
- Actually running a Morpheus session (needs Spectre fully running)
- Setting up GitHub Actions CI for the spectre repo
- Extracting forge to its own repo (instructions above, ready to execute)
- The original LangChain replacement task that spawned all of this

### Key design principle from the owner:
"I want him to know who Morpheus is." — Transparency over deception. Spectre
should know exactly what's happening, who's talking to him, and why. The owner
doesn't want tricks. He wants partnership.

---

## Git State

- **Branch:** `claude/add-claude-documentation-5xdmi`
- **Status:** Clean, all pushed, up to date with remote
- **Commits (this session):**
  ```
  2f82be0 fix(forge): bulletproof audit — fix 15 bugs across pipeline and Morpheus
  3edf2a4 feat(morpheus): add Matrix character personality and channel tagging
  7fe4103 feat: add Morpheus — Spectre's adaptive sparring partner
  2788a47 feat(forge): add performance benchmarks to stress test stage
  5adafd4 chore: add .gitignore for Forge runtime data
  771be49 feat: add Forge — multi-model autonomous development pipeline
  ```

Pull this branch on your laptop:
```bash
cd ~/spectre  # or wherever your local clone is
git fetch origin claude/add-claude-documentation-5xdmi
git checkout claude/add-claude-documentation-5xdmi
```

Everything is in `forge/`. Ready to go.