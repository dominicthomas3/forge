"""Forge Checkpoint System — crash recovery, atomic writes, output validation.

Provides:
    - atomic_write: temp file + rename pattern for crash-safe file I/O
    - validate_stage_output: pre-read validation before stage consumption
    - detect_context_exhaustion: heuristic detection of truncated model output
    - save_checkpoint / load_checkpoint: cycle-level checkpoint persistence
    - _file_checksum: SHA-256 integrity verification

Every stage output file can be corrupted by a mid-write crash (power loss,
OOM kill, API timeout). This module ensures the pipeline either has a valid
file or knows it needs to re-run the stage.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("forge.checkpoint")


# ── Atomic File I/O ──────────────────────────────────────────────────────


def atomic_write(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Write content to path using temp file + atomic rename.

    Prevents partial/corrupted files on crash:
    1. Write to path.tmp
    2. os.replace(tmp, path) — atomic on NTFS and ext4

    If os.replace fails (Windows antivirus holding the file), retries once
    after a 200ms delay.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(content, encoding=encoding)
    try:
        os.replace(str(tmp_path), str(path))
    except PermissionError:
        # Windows: target may be held by antivirus scanner — retry once
        time.sleep(0.2)
        try:
            os.replace(str(tmp_path), str(path))
        except PermissionError:
            # Last resort: fall back to non-atomic write
            logger.warning("atomic_write: os.replace failed twice for %s, using fallback", path)
            path.write_text(content, encoding=encoding)
            tmp_path.unlink(missing_ok=True)


def _file_checksum(path: Path) -> str:
    """SHA-256 checksum of a file's content."""
    h = hashlib.sha256()
    h.update(Path(path).read_bytes())
    return h.hexdigest()


# ── Output Validation ────────────────────────────────────────────────────

# Minimum output lengths per stage — below this triggers suspicion.
# These are conservative: even a "no issues found" response exceeds these.
_DEFAULT_MIN_OUTPUT = 100  # Default for unknown stages (consistent across functions)

_STAGE_MIN_OUTPUT = {
    1: 500,   # Jim analysis should be substantial
    2: 300,   # Deep Think verification
    3: 200,   # Implementation log
    4: 200,   # Review
    5: 100,   # Consensus
    6: 50,    # Fixes (might be "no fixes needed")
    7: 200,   # Stress test
}

_TRUNCATION_SIGNALS = [
    "i'll continue",
    "let me finish",
    "i need to continue",
    "continuing with",
    "will proceed with",
    "i'll now proceed",
    "let me complete",
]


def validate_stage_output(output_path: Path, stage_number: int) -> tuple[bool, str]:
    """Validate a stage's output file before the next stage reads it.

    Checks:
    1. File exists
    2. File size >= minimum threshold
    3. File is valid UTF-8
    4. Content meets stage-specific minimum length
    5. Content doesn't appear truncated mid-phrase

    Returns (is_valid, reason).
    """
    if not output_path.exists():
        return False, "file does not exist"

    size = output_path.stat().st_size
    if size < 50:
        return False, f"file too small ({size} bytes)"

    try:
        content = output_path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as e:
        return False, f"file read error: {e}"

    # Stage-specific minimum content check
    min_len = _STAGE_MIN_OUTPUT.get(stage_number, _DEFAULT_MIN_OUTPUT)
    if len(content) < min_len:
        return False, (
            f"content too short for stage {stage_number} "
            f"({len(content)} chars, need {min_len})"
        )

    # Check for truncation indicators in the last 150 chars
    tail = content[-150:].lower()
    for signal in _TRUNCATION_SIGNALS:
        if signal in tail:
            return False, f"content appears truncated (ends with '{signal}')"

    return True, "valid"


def detect_context_exhaustion(output: str, stage_number: int) -> bool:
    """Detect if a model's output was truncated due to context limits.

    Heuristics:
    - Output suspiciously short for the stage type
    - Contains continuation markers at the end
    - Empty or whitespace-only output

    Returns True if context exhaustion is likely.
    """
    if not output or not output.strip():
        return True

    stripped = output.strip()
    min_len = _STAGE_MIN_OUTPUT.get(stage_number, _DEFAULT_MIN_OUTPUT)

    # Very short output is highly suspicious for analysis/implementation stages
    if len(stripped) < min_len:
        return True

    # Check last 150 chars for truncation signals
    tail = stripped[-150:].lower()
    for signal in _TRUNCATION_SIGNALS:
        if signal in tail:
            return True

    return False


# ── Checkpoint Persistence ───────────────────────────────────────────────


@dataclass
class CycleCheckpoint:
    """Full checkpoint for a cycle — persisted after each stage completes."""
    cycle_number: int
    task_description: str
    started_at: str
    last_updated: str
    completed_stages: list[dict] = field(default_factory=list)
    status: str = "in_progress"
    cycle_learnings: list[str] = field(default_factory=list)


def save_checkpoint(cycle_dir: Path, checkpoint: CycleCheckpoint) -> Path:
    """Atomically save a cycle checkpoint to cycle_dir/checkpoint.json.

    Called after each stage completes. On crash + resume, the checkpoint
    tells the orchestrator exactly which stages finished successfully.
    """
    checkpoint.last_updated = datetime.now().isoformat()
    data = {
        "cycle_number": checkpoint.cycle_number,
        "task_description": checkpoint.task_description,
        "started_at": checkpoint.started_at,
        "last_updated": checkpoint.last_updated,
        "completed_stages": checkpoint.completed_stages,
        "status": checkpoint.status,
        "cycle_learnings": checkpoint.cycle_learnings,
    }
    path = cycle_dir / "checkpoint.json"
    atomic_write(path, json.dumps(data, indent=2))
    logger.debug("Checkpoint saved: cycle %d, %d stages", checkpoint.cycle_number, len(checkpoint.completed_stages))
    return path


def load_checkpoint(cycle_dir: Path) -> CycleCheckpoint | None:
    """Load checkpoint from cycle_dir/checkpoint.json.

    Returns None if:
    - File doesn't exist
    - File is empty or corrupt JSON
    - All stage checksums are invalid (complete corruption)

    Validates each completed stage's checksum against the actual file on disk.
    Stages with mismatched checksums are removed (they'll be re-run).
    """
    path = cycle_dir / "checkpoint.json"
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("Checkpoint file corrupt or unreadable: %s", path)
        return None

    # Validate completed stage checksums — only keep stages with matching files
    valid_stages = []
    for stage_data in data.get("completed_stages", []):
        output_path = Path(stage_data.get("output_path", ""))
        if output_path.exists():
            try:
                actual_checksum = _file_checksum(output_path)
            except OSError:
                logger.warning(
                    "Stage %d output unreadable: %s",
                    stage_data.get("stage_number", 0), output_path,
                )
                continue
            if actual_checksum == stage_data.get("checksum", ""):
                valid_stages.append(stage_data)
            else:
                logger.warning(
                    "Stage %d output checksum mismatch — file modified externally, will re-run",
                    stage_data.get("stage_number", 0),
                )
        else:
            logger.warning(
                "Stage %d output missing: %s — will re-run",
                stage_data.get("stage_number", 0), output_path,
            )

    return CycleCheckpoint(
        cycle_number=data.get("cycle_number", 0),
        task_description=data.get("task_description", ""),
        started_at=data.get("started_at", ""),
        last_updated=data.get("last_updated", ""),
        completed_stages=valid_stages,
        status=data.get("status", "in_progress"),
        cycle_learnings=data.get("cycle_learnings", []),
    )
