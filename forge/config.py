"""Forge Configuration.

Fill in your credentials below. Leave CLI paths empty for auto-detection.

Subscriptions used:
    - Google AI Pro Ultra → Gemini CLI + Deep Think SDK (no per-token cost)
    - Anthropic Claude Max → Claude Code CLI (no per-token cost)
"""

from __future__ import annotations

import os
import platform
import shutil
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ForgeConfig:
    """Pipeline configuration. Edit this file or pass overrides to the constructor."""

    # ─── CREDENTIALS ──────────────────────────────────────────────────────
    # Google API key for Deep Think SDK calls (google-genai).
    # Your Pro Ultra subscription covers the usage.
    # Get from: https://aistudio.google.com/apikey
    google_api_key: str = ""

    # ─── CLI PATHS (leave empty for auto-detection) ───────────────────────
    gemini_cli_path: str = ""
    claude_cli_path: str = ""

    # ─── TARGET PROJECT ───────────────────────────────────────────────────
    # The project Forge will analyze, modify, and test.
    target_project: Path = field(default_factory=lambda: Path.cwd())

    # ─── FORGE WORKING DIRECTORY ──────────────────────────────────────────
    # Where Forge stores cycle outputs, logs, and the morning report.
    forge_data_dir: Path = field(default_factory=lambda: Path(__file__).parent / ".forge_data")

    # ─── MODELS ───────────────────────────────────────────────────────────
    gemini_model: str = "gemini-3.1-pro-preview"
    claude_model: str = "opus"  # Max subscription — use the best
    deep_think_model: str = "gemini-3.1-pro-preview"

    # ─── SAFETY RAILS ─────────────────────────────────────────────────────
    max_cycles: int = 50          # Hard cap on build-verify cycles
    max_wall_hours: float = 8.0   # Stop before you wake up
    convergence_threshold: int = 3  # N consecutive clean verifications = done
    stress_test_rounds: int = 10  # Beat on the project repeatedly

    # ─── TIMEOUTS (seconds) ───────────────────────────────────────────────
    gemini_timeout: int = 900     # 15 min for full codebase analysis
    deep_think_timeout: int = 900  # 15 min for extended reasoning
    claude_timeout: int = 2700    # 45 min for complex multi-file implementation
    stress_timeout: int = 900     # 15 min for stress testing

    # ─── GIT ──────────────────────────────────────────────────────────────
    git_checkpoint: bool = True
    pipeline_branch: str = "forge/overnight-build"

    # ─── CODEBASE LOADING ─────────────────────────────────────────────────
    # File extensions to include when loading the codebase for Jim.
    source_extensions: tuple = (".py", ".md", ".toml", ".cfg", ".yml", ".yaml", ".txt")
    # Directories to skip when loading the codebase.
    exclude_dirs: tuple = (
        "__pycache__", ".git", ".venv", "venv", "node_modules",
        ".forge_data", ".pipeline", ".mypy_cache", ".pytest_cache",
        "eggs", ".eggs", "dist", "build", ".tox",
    )

    def __post_init__(self):
        self.target_project = Path(self.target_project).resolve()
        self.forge_data_dir = Path(self.forge_data_dir).resolve()

        # Load google_api_key from environment if not set
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass  # dotenv not installed, fall back to existing env vars

        if not self.google_api_key:
            self.google_api_key = os.environ.get("GOOGLE_API_KEY", "")

    def resolve_gemini_cli(self) -> str:
        """Find the Gemini CLI binary."""
        if self.gemini_cli_path:
            return self.gemini_cli_path
        found = shutil.which("gemini")
        if found:
            return found
        # Windows common paths
        if platform.system() == "Windows":
            candidates = [
                Path.home() / "AppData" / "Roaming" / "npm" / "gemini.cmd",
                Path.home() / "AppData" / "Roaming" / "npm" / "gemini",
                Path.home() / "AppData" / "Local" / "Programs" / "gemini" / "gemini.exe",
            ]
            for p in candidates:
                if p.exists():
                    return str(p)
        # Linux/Mac common paths
        else:
            candidates = [
                Path.home() / ".local" / "bin" / "gemini",
                Path("/usr/local/bin/gemini"),
                Path.home() / ".npm-global" / "bin" / "gemini",
            ]
            for p in candidates:
                if p.exists():
                    return str(p)
        raise FileNotFoundError(
            "Gemini CLI not found. Install it or set gemini_cli_path in config.
"
            "  npm install -g @anthropic-ai/gemini-code  (or your package manager)"
        )

    def resolve_claude_cli(self) -> str:
        """Find the Claude Code CLI binary."""
        if self.claude_cli_path:
            return self.claude_cli_path
        found = shutil.which("claude")
        if found:
            return found
        if platform.system() == "Windows":
            candidates = [
                Path.home() / ".local" / "bin" / "claude.exe",
                Path.home() / "AppData" / "Local" / "Programs" / "claude" / "claude.exe",
                Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Packages" / "claude.exe",
            ]
            for p in candidates:
                if p.exists():
                    return str(p)
        else:
            candidates = [
                Path.home() / ".local" / "bin" / "claude",
                Path("/usr/local/bin/claude"),
                Path.home() / ".claude" / "bin" / "claude",
            ]
            for p in candidates:
                if p.exists():
                    return str(p)
        raise FileNotFoundError(
            "Claude Code CLI not found. Install it or set claude_cli_path in config.
"
            "  npm install -g @anthropic-ai/claude-code  (or your package manager)"
        )

    def validate(self) -> list[str]:
        """Check configuration and return list of issues (empty = valid)."""
        issues = []
        if not self.google_api_key:
            issues.append(
                "google_api_key is not set. Deep Think stage will fail.
"
                "  Set GOOGLE_API_KEY env var or edit forge/config.py"
            )
        if not self.target_project.is_dir():
            issues.append(f"target_project does not exist: {self.target_project}")
        try:
            self.resolve_gemini_cli()
        except FileNotFoundError as e:
            issues.append(str(e))
        try:
            self.resolve_claude_cli()
        except FileNotFoundError as e:
            issues.append(str(e))
        return issues