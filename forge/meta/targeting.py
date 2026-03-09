"""File-to-category targeting: maps git diff paths to Morpheus test weights.

Static semantic mapping — no LLM call needed. Covers ~90% of cases.
The remaining 10% (utility files affecting multiple subsystems) is caught
by the mandatory minimum-1-test-per-category baseline.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from forge.meta.contracts import MorpheusTargetingConfig

logger = logging.getLogger("forge.meta.targeting")

# ── Semantic file-to-category mapping ────────────────────────────────────
# Each entry: (path_prefix, primary_category, weight_boost)
# Multiple entries can match for the same file — weights accumulate.

_FILE_CATEGORY_MAP: list[tuple[str, str, float]] = [
    # Memory / Cortex → memory testing
    ("cortex/", "memory", 0.4),
    ("memory/", "memory", 0.4),
    ("memory/lancedb", "memory", 0.2),  # Extra weight for vector store changes
    ("memory/journal", "memory", 0.1),
    ("memory/sliding_window", "memory", 0.1),
    ("memory/summarizer", "memory", 0.1),
    ("memory/consolidation", "memory", 0.1),
    ("memory/recall", "memory", 0.1),
    ("memory/contacts", "memory", 0.1),
    ("memory/knowledge_graph", "memory", 0.1),

    # Tools → tool testing
    ("tools/", "tools", 0.4),
    ("tools/registry", "tools", 0.2),
    ("tools/filesystem", "tools", 0.1),
    ("tools/reminders", "tools", 0.1),
    ("tools/supabase", "tools", 0.1),
    ("tools/web", "tools", 0.1),
    ("tools/twitter", "tools", 0.1),
    ("tools/channels", "tools", 0.1),
    ("tools/agent", "tools", 0.2),
    ("tools/operator", "tools", 0.2),
    ("tools/selector", "tools", 0.1),

    # Router / Models → routing testing
    ("core/router", "routing", 0.4),
    ("models/", "routing", 0.3),
    ("models/claude", "routing", 0.1),
    ("models/gemini", "routing", 0.1),
    ("core/agent", "routing", 0.2),  # Agent orchestrates routing
    ("core/agent", "casual", 0.1),   # Also affects casual conversation quality

    # Personality / Context → personality testing
    ("personality/", "personality", 0.4),
    ("context/compiler", "personality", 0.3),
    ("context/snapshot", "personality", 0.2),
    ("context/scheduler", "personality", 0.1),

    # Gateway → routing + edge case testing
    ("gateway/", "routing", 0.2),
    ("gateway/server", "edge", 0.1),
    ("gateway/channels", "routing", 0.1),

    # Prompt assembler → affects everything
    ("core/prompt_assembler", "casual", 0.15),
    ("core/prompt_assembler", "routing", 0.15),
    ("core/prompt_assembler", "memory", 0.1),
    ("core/prompt_assembler", "tools", 0.1),

    # Response handler → quality + personality
    ("core/response_handler", "casual", 0.2),
    ("core/response_handler", "personality", 0.1),

    # Config → edge cases
    ("config/", "edge", 0.1),
    ("config/settings", "tools", 0.1),
    ("config/models", "routing", 0.1),

    # Vision → tools + edge
    ("vision/", "tools", 0.2),
    ("vision/", "edge", 0.1),
]

# Every category gets at least this base weight (ensures no category is zero)
_BASE_WEIGHT = 0.05
_ALL_CATEGORIES = ("casual", "memory", "routing", "tools", "personality", "edge")


def get_changed_files(target_project: Path, base_ref: str = "HEAD~1") -> list[str]:
    """Get list of files changed since base_ref using git diff."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", base_ref],
            capture_output=True, text=True,
            cwd=str(target_project),
        )
        if result.returncode == 0:
            files = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
            logger.info("Changed files since %s: %d", base_ref, len(files))
            return files
    except Exception as e:
        logger.warning("Failed to get git diff: %s", e)

    # Fallback: get files changed in the last commit
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
            capture_output=True, text=True,
            cwd=str(target_project),
        )
        if result.returncode == 0:
            return [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
    except Exception:
        pass

    return []


def build_targeting_config(
    changed_files: list[str],
    forge_summary: str = "",
    iteration: int = 0,
) -> MorpheusTargetingConfig:
    """Map changed file paths to Morpheus category weights.

    Algorithm:
    1. Start with uniform base weight for all categories
    2. For each changed file, look up matching prefixes and accumulate boosts
    3. Normalize weights to sum to 1.0
    """
    weights: dict[str, float] = {cat: _BASE_WEIGHT for cat in _ALL_CATEGORIES}

    for filepath in changed_files:
        # Normalize path separators
        normalized = filepath.replace("\\", "/").lower()
        for prefix, category, boost in _FILE_CATEGORY_MAP:
            if normalized.startswith(prefix.lower()) or f"/{prefix.lower()}" in normalized:
                weights[category] = weights.get(category, _BASE_WEIGHT) + boost

    # Normalize to sum to 1.0
    total = sum(weights.values())
    if total > 0:
        weights = {cat: w / total for cat, w in weights.items()}

    logger.info("Targeting weights: %s", {k: f"{v:.2f}" for k, v in sorted(weights.items(), key=lambda x: -x[1])})

    return MorpheusTargetingConfig(
        changed_files=changed_files,
        focus_categories=weights,
        forge_summary=forge_summary,
        iteration=iteration,
    )


def weights_to_exchange_counts(
    weights: dict[str, float],
    total_exchanges: int = 30,
    min_per_category: int = 1,
) -> dict[str, int]:
    """Convert category weights to exchange counts.

    Guarantees at least min_per_category for every category.
    Distributes remaining exchanges proportionally by weight.
    """
    categories = list(weights.keys())
    if not categories:
        categories = list(_ALL_CATEGORIES)
        weights = {cat: 1.0 / len(categories) for cat in categories}

    # Guarantee minimum
    counts = {cat: min_per_category for cat in categories}
    remaining = total_exchanges - sum(counts.values())

    if remaining > 0:
        # Distribute remaining proportionally
        total_weight = sum(weights.values())
        for cat in categories:
            share = (weights.get(cat, 0) / total_weight) * remaining if total_weight > 0 else 0
            counts[cat] += int(share)

        # Distribute rounding remainder to highest-weight categories
        distributed = sum(counts.values())
        leftover = total_exchanges - distributed
        sorted_cats = sorted(categories, key=lambda c: weights.get(c, 0), reverse=True)
        for i in range(leftover):
            counts[sorted_cats[i % len(sorted_cats)]] += 1

    return counts
