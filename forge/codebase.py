"""Codebase loader — concatenates an entire project into a single string.

Optimized for Gemini's 1M token context window. Includes file path headers
so the model can reference specific files and line numbers.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

from forge.config import ForgeConfig


def _is_sensitive_file(path: Path, config: ForgeConfig) -> bool:
    """Check if a file should be excluded for security reasons.

    Matches against exact filenames and glob patterns defined in config.
    Prevents secrets, credentials, and private keys from being loaded
    into LLM context windows.
    """
    name = path.name
    # Exact filename match
    if name in config.exclude_filenames:
        return True
    # Pattern match (e.g., *.pem, *.key)
    for pattern in config.exclude_filename_patterns:
        if fnmatch.fnmatch(name, pattern):
            return True
    return False


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
        # Skip sensitive files (secrets, credentials, keys)
        if _is_sensitive_file(path, config):
            continue
        # Skip binary files (quick heuristic)
        if path.suffix in {".pyc", ".pyo", ".so", ".dll", ".exe", ".bin", ".dat"}:
            continue

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            rel_path = path.relative_to(root)
            line_count = content.count("\\n") + 1
            total_lines += line_count
            parts.append(
                f'\\n<file path="{rel_path}" lines="{line_count}">\\n'
                f"{content}"
                f"</file>"
            )
            files_loaded += 1
        except Exception:
            continue

    header = (
        f"# FULL CODEBASE — {files_loaded} files, {total_lines:,} total lines\\n"
        f"# Project: {root.name} ({root})\\n"
        f"# Extensions: {', '.join(config.source_extensions)}\\n"
    )
    return header + "\\n".join(parts)


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
        if _is_sensitive_file(path, config):
            continue

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            line_count = content.count("\\n") + 1
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