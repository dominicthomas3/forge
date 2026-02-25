"""Codebase loader — concatenates an entire project into a single string.

Optimized for Gemini's 1M token context window. Includes file path headers
so the model can reference specific files and line numbers.
"""

from __future__ import annotations

from pathlib import Path

from forge.config import ForgeConfig


def load_codebase(config: ForgeConfig) -> str:
    """Load the entire target project into a single string.

    Returns a concatenated string with file path headers, suitable for
    feeding to Gemini's 1M context window.
    """
    root = config.target_project
    parts: list[str] = []
    files_loaded = 0
    total_lines = 0

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        # Skip excluded directories
        if any(excluded in path.parts for excluded in config.exclude_dirs):
            continue
        # Skip non-source files
        if path.suffix not in config.source_extensions:
            continue
        # Skip binary files (quick heuristic)
        if path.suffix in {".pyc", ".pyo", ".so", ".dll", ".exe", ".bin", ".dat"}:
            continue

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            rel_path = path.relative_to(root)
            line_count = content.count("
") + 1
            total_lines += line_count
            parts.append(
                f"
{'=' * 80}
"
                f"# FILE: {rel_path} ({line_count} lines)
"
                f"{'=' * 80}
"
                f"{content}"
            )
            files_loaded += 1
        except Exception:
            continue

    header = (
        f"# FULL CODEBASE — {files_loaded} files, {total_lines:,} total lines
"
        f"# Project: {root.name} ({root})
"
        f"# Extensions: {', '.join(config.source_extensions)}
"
    )
    return header + "
".join(parts)


def load_codebase_python_only(config: ForgeConfig) -> str:
    """Load only Python files — smaller context for focused analysis."""
    original_extensions = config.source_extensions
    try:
        config.source_extensions = (".py",)
        return load_codebase(config)
    finally:
        config.source_extensions = original_extensions


def get_codebase_stats(config: ForgeConfig) -> dict:
    """Quick stats about the target codebase without loading it."""
    root = config.target_project
    stats = {"files": 0, "lines": 0, "estimated_tokens": 0, "by_extension": {}}

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(excluded in path.parts for excluded in config.exclude_dirs):
            continue
        if path.suffix not in config.source_extensions:
            continue

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            line_count = content.count("
") + 1
            stats["files"] += 1
            stats["lines"] += line_count
            ext = path.suffix
            if ext not in stats["by_extension"]:
                stats["by_extension"][ext] = {"files": 0, "lines": 0}
            stats["by_extension"][ext]["files"] += 1
            stats["by_extension"][ext]["lines"] += line_count
        except Exception:
            continue

    # Rough token estimate: ~1 token per 3.5 characters for code
    stats["estimated_tokens"] = stats["lines"] * 25  # ~25 tokens per line average
    return stats