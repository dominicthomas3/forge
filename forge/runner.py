"""Forge Runner — invokes Gemini CLI, Claude Code CLI, and Deep Think SDK.

All calls run on subscriptions (Claude Max + Google Pro Ultra).
Zero marginal cost per invocation.
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

from forge.config import ForgeConfig

logger = logging.getLogger("forge.runner")

# Maximum characters to pass via -p flag before switching to stdin piping.
# Windows CreateProcess has a ~8191 char limit for the entire command line.
_MAX_ARG_LENGTH = 7000


class RunnerError(Exception):
    """Raised when a CLI or SDK call fails."""

    def __init__(self, message: str, stdout: str = "", stderr: str = ""):
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


class Runner:
    """Invokes external AI models via CLI and SDK."""

    def __init__(self, config: ForgeConfig):
        self.config = config
        self._gemini_bin: str | None = None
        self._claude_bin: str | None = None
        self._deep_think_client = None

    # ── Gemini CLI (Jim) ──────────────────────────────────────────────────

    def run_gemini(self, prompt: str, timeout: int | None = None) -> str:
        """Run Gemini 3.1 Pro via CLI. Pro Ultra subscription — no cost.

        Handles long prompts by writing to a temp file and piping via stdin.
        """
        if timeout is None:
            timeout = self.config.gemini_timeout

        if self._gemini_bin is None:
            self._gemini_bin = self.config.resolve_gemini_cli()

        logger.info("Running Gemini CLI (Jim) — %d chars prompt", len(prompt))

        max_retries = 3
        retry_count = 0

        while retry_count < max_retries:
            start = time.time()
            try:
                if len(prompt) <= _MAX_ARG_LENGTH:
                    # Short prompt — pass directly via -p
                    cmd = [
                        self._gemini_bin,
                        "-p", prompt,
                        "-m", self.config.gemini_model,
                        "-o", "text",
                        "-y",
                    ]
                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        timeout=timeout,
                        cwd=str(self.config.target_project),
                        encoding="utf-8",
                        errors="replace",
                    )
                else:
                    # Long prompt — pipe via stdin.
                    cmd = [
                        self._gemini_bin,
                        "-m", self.config.gemini_model,
                        "-o", "text",
                        "-y",
                    ]
                    result = subprocess.run(
                        cmd,
                        input=prompt,
                        capture_output=True,
                        text=True,
                        timeout=timeout,
                        cwd=str(self.config.target_project),
                        encoding="utf-8",
                        errors="replace",
                    )

                elapsed = time.time() - start
                
                # Check for 429 or other specific failure strings in output
                output = result.stdout.strip()
                error_output = result.stderr.strip()
                
                is_429 = "RESOURCE_EXHAUSTED" in error_output or "429" in error_output or "No capacity available" in error_output
                
                if result.returncode != 0 or is_429 or len(output) < 100:
                    retry_count += 1
                    wait_time = 120 if is_429 else 60
                    
                    error_msg = f"Gemini CLI failed (exit {result.returncode})"
                    if is_429:
                        error_msg = "Gemini CLI hit RATE LIMIT (429 Resource Exhausted)"
                    elif len(output) < 100 and result.returncode == 0:
                        error_msg = f"Gemini CLI returned suspiciously short output ({len(output)} chars)"
                    
                    logger.error("%s. Retry %d/%d in %ds...", error_msg, retry_count, max_retries, wait_time)
                    if error_output:
                        logger.debug("Gemini Stderr: %s", error_output[:500])
                        
                    time.sleep(wait_time)
                    continue
                
                logger.info("Gemini CLI completed in %.1fs (exit %d)", elapsed, result.returncode)
                return output

            except subprocess.TimeoutExpired:
                retry_count += 1
                logger.error("Gemini CLI timed out after %d seconds. Retry %d/%d...", timeout, retry_count, max_retries)
                time.sleep(60)
                continue

        raise RunnerError(f"Gemini CLI failed after {max_retries} retries.")

    # ── Claude Code CLI ───────────────────────────────────────────────────

    def run_claude(self, prompt: str, timeout: int | None = None) -> str:
        """Run Claude Code via CLI. Max subscription — no cost.

        Claude has file system access and can edit files in the target project.
        """
        if timeout is None:
            timeout = self.config.claude_timeout

        if self._claude_bin is None:
            self._claude_bin = self.config.resolve_claude_cli()

        logger.info("Running Claude Code CLI — %d chars prompt", len(prompt))

        # We pass the prompt as an argument because Claude Code CLI with --print 
        # often requires it as a positional argument on Windows to work correctly 
        # with stdout capturing.
        
        # Base command
        cmd = [
            self._claude_bin,
            "--print",
            "--output-format", "text",
            "--model", self.config.claude_model,
            "--dangerously-skip-permissions",
            "--verbose",
        ]

        while True:
            start = time.time()
            try:
                if len(prompt) <= _MAX_ARG_LENGTH:
                    # Short prompt — pass as argument
                    current_cmd = cmd + [prompt]
                    result = subprocess.run(
                        current_cmd,
                        capture_output=True,
                        text=True,
                        timeout=timeout,
                        cwd=str(self.config.target_project),
                        encoding="utf-8",
                        errors="replace",
                    )
                else:
                    # Long prompt — try piping first
                    result = subprocess.run(
                        cmd,
                        input=prompt,
                        capture_output=True,
                        text=True,
                        timeout=timeout,
                        cwd=str(self.config.target_project),
                        encoding="utf-8",
                        errors="replace",
                    )
                    
                    # If piping failed because of the "Input must be provided" error,
                    # we have a problem with very long prompts on Windows.
                    if result.returncode != 0 and "Input must be provided" in result.stderr:
                        logger.warning("Claude stdin piping failed, trying to pass via temp file reference")
                        prompt_file = self.config.forge_data_dir / "_temp_claude_prompt.txt"
                        prompt_file.parent.mkdir(parents=True, exist_ok=True)
                        prompt_file.write_text(prompt, encoding="utf-8")
                        
                        file_prompt = f"Please read the instructions in {prompt_file} and execute them."
                        current_cmd = cmd + [file_prompt]
                        result = subprocess.run(
                            current_cmd,
                            capture_output=True,
                            text=True,
                            timeout=timeout,
                            cwd=str(self.config.target_project),
                            encoding="utf-8",
                            errors="replace",
                        )

                elapsed = time.time() - start
                logger.info("Claude CLI completed in %.1fs (exit %d)", elapsed, result.returncode)

                if result.returncode != 0:
                    # Claude CLI might return non-zero but still have useful output.
                    # Only retry if it looks like a fatal error (no stdout).
                    if not result.stdout.strip():
                        logger.error(
                            "Claude CLI failed (exit %d): %s. Retrying in 60 seconds...",
                            result.returncode, (result.stderr or result.stdout)[:500]
                        )
                        time.sleep(60)
                        continue
                    logger.warning(
                        "Claude CLI returned non-zero (%d) but produced output — using it",
                        result.returncode,
                    )
                break
            except subprocess.TimeoutExpired:
                logger.error("Claude CLI timed out after %d seconds. Retrying in 60 seconds...", timeout)
                time.sleep(60)
                continue

        return result.stdout

    # ── Deep Think (google-genai SDK) ─────────────────────────────────────

    def run_deep_think(self, prompt: str, system: str = "", timeout: int | None = None) -> str:
        """Run Gemini Deep Think via google-genai SDK. Pro Ultra — no cost.

        Uses ThinkingLevel.HIGH for extended reasoning chains.
        Best with detailed 1,000-3,000 word prompts crafted by Claude.
        """
        if timeout is None:
            timeout = self.config.deep_think_timeout

        logger.info("Running Deep Think — %d chars prompt (timeout %ds)", len(prompt), timeout)
        start = time.time()

        if self._deep_think_client is None:
            from google import genai

            if not self.config.google_api_key:
                raise RunnerError(
                    "google_api_key required for Deep Think. "
                    "Set GOOGLE_API_KEY env var or edit forge/config.py"
                )
            self._deep_think_client = genai.Client(api_key=self.config.google_api_key)

        from google.genai import types

        config_kwargs = {
            "thinking_config": types.ThinkingConfig(
                thinking_level=types.ThinkingLevel.HIGH,
            ),
            "http_options": types.HttpOptions(timeout=timeout * 1000),
        }
        if system:
            config_kwargs["system_instruction"] = system

        while True:
            try:
                response = self._deep_think_client.models.generate_content(
                    model=self.config.deep_think_model,
                    contents=prompt,
                    config=types.GenerateContentConfig(**config_kwargs),
                )
                break
            except Exception as e:
                logger.error("Deep Think failed: %s. Retrying in 10 minutes (600s)...", e)
                time.sleep(600)
                # Reset start time for accurate elapsed calculation of the successful run
                start = time.time()

        elapsed = time.time() - start

        # Check if thinking was actually used
        thinking_used = False
        if response.candidates:
            for candidate in response.candidates:
                if candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if hasattr(part, "thought") and part.thought:
                            thinking_used = True
                            break

        logger.info(
            "Deep Think completed in %.1fs (thinking: %s)",
            elapsed,
            "HIGH" if thinking_used else "standard",
        )

        try:
            return response.text or "(No response generated)"
        except ValueError:
            return "(Response blocked by safety filters)"

    # ── Convenience: Claude for prompt crafting ───────────────────────────

    def claude_craft_prompt(self, context: str, target_instructions: str) -> str:
        """Have Claude craft a detailed prompt for Deep Think.

        The user found that sending Deep Think detailed 1,000-3,000 word
        prompts crafted by Claude produces significantly better results
        than sending raw context directly.
        """
        meta_prompt = f"""You are crafting a prompt for Gemini Deep Think (extended reasoning mode).

Deep Think works best with detailed, structured prompts between 1,000 and 3,000 words.
It needs:
- Clear context about what it's analyzing
- Specific questions to reason about
- Explicit constraints and requirements
- Expected output format

Your job: Read the CONTEXT below, then craft a comprehensive prompt that will get
the best possible analysis from Deep Think.

CONTEXT:
{context}

WHAT DEEP THINK SHOULD DO:
{target_instructions}

CRAFT THE PROMPT NOW. Output ONLY the prompt text — nothing else. No preamble,
no explanation, just the prompt that will be sent directly to Deep Think.
Make it 1,000-3,000 words. Be specific, structured, and thorough."""

        logger.info("Claude crafting prompt for Deep Think")
        return self.run_claude(meta_prompt, timeout=900)