"""UpdateManager — polls Supabase for OTA updates.

Periodically checks forge-updates/latest.json in Supabase Storage.
When a new update is published:
  1. Downloads the tarball from Supabase Storage
  2. Verifies SHA-256 hash
  3. Extracts to a versioned directory
  4. Installs new requirements if changed
  5. Performs a graceful restart

Pattern replicated from Spectre's proven OTA system.
"""

import asyncio
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Tuple

_TARFILE_HAS_FILTER = sys.version_info >= (3, 12)

import httpx

from forge.version import FORGE_VERSION

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _parse_version(v: str) -> Tuple[int, ...]:
    """Parse a version string like '0.1.2' into a comparable tuple."""
    try:
        return tuple(int(x) for x in v.strip().split("."))
    except (ValueError, AttributeError):
        return (0,)


def _safe_version(v: str) -> str:
    """Sanitize a version string to prevent path traversal."""
    v = v.strip()
    if not _VERSION_RE.match(v):
        raise ValueError(f"Invalid version string: {v!r}")
    return v


class UpdateManager:
    """Manages OTA updates for Forge."""

    def __init__(
        self,
        supabase_url: str,
        supabase_key: str,
        project_root: Path,
        shutdown_event: asyncio.Event | None = None,
    ):
        self.supabase_url = supabase_url.rstrip("/")
        self.supabase_key = supabase_key
        self.project_root = project_root
        # When running from an extracted version (forge_versions/X.Y.Z/),
        # project_root.parent IS forge_versions — don't nest it.
        if project_root.parent.name == "forge_versions":
            self.versions_dir = project_root.parent
        else:
            self.versions_dir = project_root.parent / "forge_versions"
        self.current_version = FORGE_VERSION
        self.pending_version: str | None = None
        self._running = False
        self._poll_interval = 300  # Check every 5 minutes
        self._shutdown_event = shutdown_event

    @property
    def headers(self) -> dict:
        return {
            "apikey": self.supabase_key,
            "Authorization": f"Bearer {self.supabase_key}",
        }

    async def check_for_update(self) -> dict | None:
        """Check forge-updates/latest.json in Supabase Storage for a newer version."""
        url = f"{self.supabase_url}/storage/v1/object/public/forge-updates/latest.json"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
            if resp.status_code == 404:
                return None
            if resp.status_code != 200:
                print(f"[update] Check failed: {resp.status_code}")
                return None

            try:
                latest = resp.json()
            except Exception:
                print("[update] Failed to parse latest.json")
                return None

            version = latest.get("version", "")
            if not version:
                return None

            if _parse_version(version) <= _parse_version(self.current_version):
                return None

            print(f"[update] New version available: {version} (current: {self.current_version})")
            return latest

    async def download_update(self, update: dict) -> Path | None:
        """Download the update tarball from Supabase Storage with streaming."""
        storage_path = update["storage_path"]

        # Validate storage_path against path traversal attacks
        if ".." in storage_path or storage_path.startswith("/") or "://" in storage_path:
            print(f"[update] Rejecting update: suspicious storage_path: {storage_path}")
            return None

        download_url = f"{self.supabase_url}/storage/v1/object/forge-updates/{storage_path}"

        expected_hash = update.get("sha256_hash")
        if not expected_hash:
            print("[update] Rejecting update: no SHA-256 hash provided (required)")
            return None

        tmp_dir = Path(tempfile.mkdtemp(prefix="forge_update_"))
        tarball_path = tmp_dir / f"forge-{update['version']}.tar.gz"

        print(f"[update] Downloading {download_url}...")
        async with httpx.AsyncClient(timeout=300) as client:
            async with client.stream("GET", download_url, headers=self.headers) as resp:
                if resp.status_code != 200:
                    print(f"[update] Download failed: {resp.status_code}")
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    return None

                with open(tarball_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        f.write(chunk)

        # Verify SHA-256 (mandatory)
        actual_hash = hashlib.sha256(tarball_path.read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            print(f"[update] Hash mismatch! Expected {expected_hash}, got {actual_hash}")
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return None
        print(f"[update] Hash verified: {actual_hash[:16]}...")

        return tarball_path

    def extract_update(self, tarball_path: Path, version: str) -> Path:
        """Extract the tarball to a versioned directory."""
        target_dir = self.versions_dir / version
        target_dir.mkdir(parents=True, exist_ok=True)

        with tarfile.open(tarball_path, "r:gz") as tar:
            if _TARFILE_HAS_FILTER:
                tar.extractall(path=target_dir, filter="data")
            else:
                # Manual zip-slip protection for Python < 3.12
                resolved_target = str(target_dir.resolve())
                for member in tar.getmembers():
                    member_path = (target_dir / member.name).resolve()
                    if not str(member_path).startswith(resolved_target):
                        raise ValueError(f"Path traversal detected in tar member: {member.name}")
                    if member.issym() or member.islnk():
                        raise ValueError(f"Symlink in tar archive rejected: {member.name}")
                tar.extractall(path=target_dir)

        # Copy .env from project root so the new version can find its config
        env_file = self.project_root / ".env"
        if env_file.exists():
            shutil.copy2(env_file, target_dir / ".env")
            print(f"[update] Copied .env to {target_dir}")

        # Auto-inject FORGE_DATA_DIR so pipeline data persists across versions
        self._inject_data_dir(target_dir)

        print(f"[update] Extracted to {target_dir}")
        return target_dir

    def _inject_data_dir(self, target_dir: Path) -> None:
        """Ensure FORGE_DATA_DIR is set in the new version's .env."""
        data_dir = self._read_data_dir_from_env() or str(self.project_root.resolve())

        env_path = target_dir / ".env"
        if env_path.exists():
            lines = env_path.read_text(encoding="utf-8").splitlines()
            lines = [l for l in lines if not l.startswith("FORGE_DATA_DIR=")]
        else:
            lines = []

        lines.append(f"FORGE_DATA_DIR={data_dir}")
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"[update] Injected FORGE_DATA_DIR={data_dir}")

    def _read_data_dir_from_env(self) -> str:
        """Read FORGE_DATA_DIR from the current .env file or os.environ."""
        from_env = os.environ.get("FORGE_DATA_DIR", "")
        if from_env:
            return from_env
        env_file = self.project_root / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("FORGE_DATA_DIR=") and not line.startswith("#"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        return ""

    def install_requirements(self, update_dir: Path) -> bool:
        """Install new requirements if pyproject.toml dependencies changed."""
        new_toml = update_dir / "pyproject.toml"
        if not new_toml.exists():
            return True

        # Use pip install -e . for editable install from the new version
        print("[update] Installing updated dependencies...")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", str(update_dir), "-q"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"[update] pip install failed: {result.stderr}")
            return False

        print("[update] Dependencies installed successfully")
        return True

    async def apply_update(self, update: dict) -> bool:
        """Full update cycle: download, verify, extract, install, restart."""
        try:
            version = _safe_version(update["version"])
        except ValueError as e:
            print(f"[update] Rejecting update: {e}")
            return False

        tarball = await self.download_update(update)
        if not tarball:
            return False
        update_dir = self.extract_update(tarball, version)

        # Clean up tarball temp dir
        shutil.rmtree(tarball.parent, ignore_errors=True)

        # Install new dependencies
        if not self.install_requirements(update_dir):
            print("[update] Failed to install dependencies, cleaning up partial update")
            shutil.rmtree(update_dir, ignore_errors=True)
            return False

        # Write current version pointer
        version_file = self.versions_dir / "current"
        version_file.parent.mkdir(parents=True, exist_ok=True)
        version_file.write_text(version)

        # Report success to Supabase (non-blocking)
        try:
            await self._report_update_applied(version)
        except Exception as e:
            print(f"[update] Failed to report update to Supabase (non-fatal): {e}")

        self.pending_version = version

        # Graceful restart
        print(f"[update] Update to v{version} complete. Restarting...")
        self._restart()
        return True

    async def _report_update_applied(self, version: str) -> None:
        """Report successful update to Supabase."""
        from datetime import datetime, timezone

        url = f"{self.supabase_url}/rest/v1/forge_versions"
        async with httpx.AsyncClient(timeout=10) as client:
            # Mark old versions as not current
            await client.patch(
                f"{url}?is_current=eq.true",
                headers={**self.headers, "Content-Type": "application/json"},
                json={"is_current": False},
            )
            # Insert new version
            await client.post(
                url,
                headers={
                    **self.headers,
                    "Content-Type": "application/json",
                    "Prefer": "resolution=merge-duplicates",
                },
                json=[{
                    "version": version,
                    "codename": f"OTA-{version}",
                    "changelog": f"Auto-updated to v{version}",
                    "deployed_at": datetime.now(timezone.utc).isoformat(),
                    "is_current": True,
                    "project": "forge",
                }],
            )

    def _restart(self) -> None:
        """Signal to exit so the watchdog can restart from the new version."""
        self._running = False
        if self._shutdown_event:
            self._shutdown_event.set()
        else:
            raise RuntimeError("UpdateManager requires shutdown_event for safe restart")

    async def start_polling(self) -> None:
        """Poll for updates periodically."""
        self._running = True
        print(f"[update] Update manager started (checking every {self._poll_interval}s)")

        while self._running:
            try:
                update = await self.check_for_update()
                if update and await self.apply_update(update):
                    break
            except Exception as e:
                print(f"[update] Error during update check: {e}")

            await asyncio.sleep(self._poll_interval)

    def stop(self) -> None:
        """Stop the update polling loop."""
        self._running = False
