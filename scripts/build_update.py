#!/usr/bin/env python3
"""Build a local Forge update tarball for testing.

Usage:
    python scripts/build_update.py

Creates:
    dist/forge-{version}.tar.gz
    dist/forge-{version}.sha256
"""

import hashlib
import tarfile
from pathlib import Path

from forge.version import FORGE_VERSION

# Directories and files to include in the tarball
INCLUDE_DIRS = [
    "forge",
    "update",
    "scripts",
]

INCLUDE_FILES = [
    "pyproject.toml",
    "requirements.txt",
    "GEMINI.md",
]

# Patterns to exclude
EXCLUDE_PATTERNS = {
    "__pycache__",
    ".pyc",
    ".pyo",
    ".env",
    ".git",
    ".github",
    ".forge_data",
    "forge_versions",
    ".venv",
    "venv",
    "dist",
    ".mypy_cache",
    ".pytest_cache",
}


def should_exclude(path: Path) -> bool:
    """Check if a path should be excluded from the tarball."""
    parts = path.parts
    for part in parts:
        for pattern in EXCLUDE_PATTERNS:
            if pattern in part:
                return True
    return False


def main():
    project_root = Path(__file__).resolve().parent.parent
    dist_dir = project_root / "dist"
    dist_dir.mkdir(exist_ok=True)

    version = FORGE_VERSION
    tarball_name = f"forge-{version}.tar.gz"
    tarball_path = dist_dir / tarball_name

    print(f"Building Forge v{version} tarball...")

    with tarfile.open(tarball_path, "w:gz") as tar:
        # Add directories
        for dir_name in INCLUDE_DIRS:
            dir_path = project_root / dir_name
            if not dir_path.exists():
                print(f"  Warning: {dir_name}/ not found, skipping")
                continue
            for file_path in sorted(dir_path.rglob("*")):
                if file_path.is_file() and not should_exclude(file_path):
                    arcname = str(file_path.relative_to(project_root))
                    tar.add(file_path, arcname=arcname)
                    print(f"  + {arcname}")

        # Add individual files
        for file_name in INCLUDE_FILES:
            file_path = project_root / file_name
            if file_path.exists():
                tar.add(file_path, arcname=file_name)
                print(f"  + {file_name}")

    # Calculate SHA-256
    sha256 = hashlib.sha256(tarball_path.read_bytes()).hexdigest()
    hash_path = dist_dir / f"forge-{version}.sha256"
    hash_path.write_text(f"{sha256}  {tarball_name}\n")

    print(f"\nBuild complete:")
    print(f"  Tarball: {tarball_path}")
    print(f"  Size:    {tarball_path.stat().st_size:,} bytes")
    print(f"  SHA-256: {sha256}")


if __name__ == "__main__":
    main()
