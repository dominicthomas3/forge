"""Forge Runner — invokes Gemini CLI, Claude Code CLI, and Deep Think SDK.

All calls run on subscriptions (Claude Max + Google Pro Ultra).
Zero marginal cost per invocation.

Uses Popen with active polling instead of blocking subprocess.run().
Every subprocess is monitored with:
    - Status logs every 30 seconds
    - Warnings when exceeding expected duration
    - Hard kill when clearly hung
    - Stall detection (process alive but not producing output)
"""

from __future__ import annotations

import logging
import os
import platform
import signal
import subprocess
import threading
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
    """Invokes external AI models via CLI and SDK with active monitoring."""

    def __init__(self, config: ForgeConfig):
        self.config = config
        self._gemini_bin: str | None = None
        self._claude_bin: str | None = None
        self._deep_think_client = None

    # ── Monitored Subprocess Execution ─────────────────────────────────────

    def _run_monitored(
        self,
        cmd: list[str],
        *,
        input_text: str | None = None,
        timeout: int,
        stage_name: str,
        expected_min: int = 60,
        expected_max: int = 300,
        stall_limit: int = 180,
        cwd: str,
    ) -> subprocess.CompletedProcess:
        """Run a subprocess with active polling and monitoring.

        Args:
            cmd: Command to execute.
            input_text: Optional stdin text to pipe.
            timeout: Hard kill timeout in seconds.
            stage_name: Human-readable name for logging (e.g., "Gemini/Jim").
            expected_min: Minimum expected duration (seconds). Below this = fast.
            expected_max: Expected max duration. Exceeding triggers a warning.
            stall_limit: If process is alive but no new output for this many
                         seconds, log a stall warning.
            cwd: Working directory.

        Returns:
            CompletedProcess with stdout, stderr, returncode.

        Raises:
            subprocess.TimeoutExpired: If hard timeout exceeded (process killed).
        """
        logger.info("[%s] Starting subprocess (timeout=%ds, expected=%d-%ds)",
                     stage_name, timeout, expected_min, expected_max)

        # Always use PIPE for stdin — never inherit parent stdin.
        # Under nohup, parent stdin is /dev/null which confuses CLIs
        # that check stdin for input.
        #
        # Strip ALL Claude Code env vars so CLI subprocesses don't detect
        # a parent session and refuse to launch or behave unexpectedly.
        clean_env = os.environ.copy()
        for key in list(clean_env.keys()):
            key_upper = key.upper()
            if key_upper == "CLAUDECODE" or key_upper.startswith("CLAUDE_CODE_"):
                del clean_env[key]

        # Create a new process group so we can kill the entire tree on timeout.
        # Without this, child processes (node, python subprocesses) survive
        # proc.kill() and leak as orphans.
        popen_kwargs: dict = {}
        if platform.system() == "Windows":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True

        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
            env=clean_env,
            encoding="utf-8",
            errors="replace",
            **popen_kwargs,
        )

        # ── I/O threads to prevent pipe-buffer deadlock ──
        # The classic subprocess deadlock: if we use stdout=PIPE + stderr=PIPE
        # but only call proc.poll() without reading the pipes, the OS pipe
        # buffer (4-64KB on Windows) fills up, the child blocks on write,
        # and our poll loop sees it as "still running" forever.
        # Fix: drain stdout/stderr continuously in background threads.

        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []

        def _write_stdin():
            try:
                if input_text:
                    proc.stdin.write(input_text)
                proc.stdin.close()
            except Exception:
                pass  # Process may have died

        def _drain(pipe, chunks):
            try:
                for line in pipe:
                    chunks.append(line)
            except Exception:
                pass

        stdin_thread = threading.Thread(target=_write_stdin, daemon=True)
        stdout_thread = threading.Thread(target=_drain, args=(proc.stdout, stdout_chunks), daemon=True)
        stderr_thread = threading.Thread(target=_drain, args=(proc.stderr, stderr_chunks), daemon=True)
        stdin_thread.start()
        stdout_thread.start()
        stderr_thread.start()

        start = time.time()
        warned_slow = False
        warned_stall = False
        poll_interval = 0.1  # Start fast, ramp up (adaptive polling)
        poll_max = 5.0       # Cap at 5s for long-running processes
        poll_multiplier = 1.5
        last_log_time = start

        while True:
            retcode = proc.poll()
            if retcode is not None:
                # Process finished — wait for I/O threads to drain
                stdout_thread.join(timeout=10)
                stderr_thread.join(timeout=10)
                stdout = "".join(stdout_chunks)
                stderr = "".join(stderr_chunks)
                elapsed = time.time() - start
                logger.info("[%s] Finished in %.1fs (exit %d)", stage_name, elapsed, retcode)
                return subprocess.CompletedProcess(
                    args=cmd, returncode=retcode, stdout=stdout, stderr=stderr,
                )

            elapsed = time.time() - start

            # Periodic status log — every 30 seconds
            if elapsed - (last_log_time - start) >= 30:
                logger.info("[%s] Still running... %.0fs elapsed", stage_name, elapsed)
                last_log_time = time.time()

            # Warning: exceeding expected max duration
            if elapsed > expected_max and not warned_slow:
                logger.warning(
                    "[%s] SLOW — %.0fs elapsed (expected <%ds). "
                    "Will hard-kill at %ds if no response.",
                    stage_name, elapsed, expected_max, timeout,
                )
                warned_slow = True

            # Stall detection: process alive way past expected, likely hung
            if elapsed > expected_max + stall_limit and not warned_stall:
                logger.warning(
                    "[%s] POSSIBLE STALL — %.0fs elapsed, %ds past expected max. "
                    "Process PID %d still alive.",
                    stage_name, elapsed, int(elapsed - expected_max), proc.pid,
                )
                warned_stall = True

            # Hard timeout — kill the process
            if elapsed > timeout:
                logger.error(
                    "[%s] HARD TIMEOUT — killing process after %.0fs (limit: %ds)",
                    stage_name, elapsed, timeout,
                )
                self._kill_process_tree(proc)
                stdout_thread.join(timeout=5)
                stderr_thread.join(timeout=5)
                stdout = "".join(stdout_chunks)
                stderr = "".join(stderr_chunks)
                raise subprocess.TimeoutExpired(
                    cmd=cmd, timeout=timeout, output=stdout, stderr=stderr,
                )

            time.sleep(poll_interval)
            # Adaptive: ramp up interval so fast ops finish in <1s
            # but long-running processes don't burn CPU spinning.
            poll_interval = min(poll_interval * poll_multiplier, poll_max)

    @staticmethod
    def _kill_process_tree(proc: subprocess.Popen) -> None:
        """Kill a process and all its children (the entire process tree).

        On Windows, uses `taskkill /F /T /PID` which kills the tree.
        On Unix, uses `os.killpg` to signal the whole process group.
        Falls back to `proc.kill()` if tree kill fails.
        """
        try:
            if platform.system() == "Windows":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    timeout=10,
                )
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            # Fallback: at least kill the root process
            try:
                proc.kill()
            except Exception:
                pass

    # ── Gemini CLI (Jim) ──────────────────────────────────────────────────

    def run_gemini(self, prompt: str, timeout: int | None = None, blueprint: str = "full") -> str:
        """Run Gemini 3.1 Pro via CLI. Pro Ultra subscription — no cost.

        Handles long prompts by writing to a temp file and piping via stdin.

        Args:
            prompt: The prompt text.
            timeout: Hard timeout in seconds.
            blueprint: Intelligence level — "full" for analysis, "compact" for
                reviews/generation, "none" to skip.
        """
        if timeout is None:
            timeout = self.config.gemini_timeout

        if self._gemini_bin is None:
            self._gemini_bin = self.config.resolve_gemini_cli()

        # Inject Jim Intelligence Blueprint — teaches Gemini to think step-by-step,
        # verify claims, cite sources, and challenge its own output.
        if self.config.worker_intelligence and blueprint != "none":
            from forge.worker_blueprint import JIM_BLUEPRINT, JIM_BLUEPRINT_COMPACT
            preamble = JIM_BLUEPRINT if blueprint == "full" else JIM_BLUEPRINT_COMPACT
            prompt = preamble + prompt

        logger.info("Running Gemini CLI (Jim) — %d chars prompt (blueprint=%s)", len(prompt), blueprint)

        # Use a neutral cwd so the Gemini CLI doesn't auto-scan/index the
        # target project.  Jim already receives the full codebase via stdin —
        # double-loading it through CLI project scanning was causing hangs on
        # large codebases (>1M chars).
        import tempfile
        neutral_cwd = tempfile.gettempdir()

        max_retries = 3
        retry_count = 0

        while retry_count < max_retries:
            try:
                if len(prompt) <= _MAX_ARG_LENGTH:
                    cmd = [
                        self._gemini_bin,
                        "-p", prompt,
                        "-m", self.config.gemini_model,
                        "-o", "text",
                        "-y",
                    ]
                    result = self._run_monitored(
                        cmd,
                        timeout=timeout,
                        stage_name="Gemini/Jim",
                        expected_min=30,
                        expected_max=600,   # 10 min expected max for large codebases
                        stall_limit=300,    # 5 min past expected = stall warning
                        cwd=neutral_cwd,
                    )
                else:
                    cmd = [
                        self._gemini_bin,
                        "-m", self.config.gemini_model,
                        "-o", "text",
                        "-y",
                    ]
                    result = self._run_monitored(
                        cmd,
                        input_text=prompt,
                        timeout=timeout,
                        stage_name="Gemini/Jim",
                        expected_min=30,
                        expected_max=600,
                        stall_limit=300,
                        cwd=neutral_cwd,
                    )

                output = result.stdout.strip()
                error_output = result.stderr.strip()

                is_429 = (
                    "RESOURCE_EXHAUSTED" in error_output
                    or "429" in error_output
                    or "No capacity available" in error_output
                )

                if result.returncode != 0 or is_429 or len(output) < 20:
                    retry_count += 1
                    wait_time = 120 if is_429 else 60

                    error_msg = f"Gemini CLI failed (exit {result.returncode})"
                    if is_429:
                        error_msg = "Gemini CLI hit RATE LIMIT (429 Resource Exhausted)"
                    elif len(output) < 20 and result.returncode == 0:
                        error_msg = f"Gemini CLI returned suspiciously short output ({len(output)} chars)"

                    logger.error("%s. Retry %d/%d in %ds...", error_msg, retry_count, max_retries, wait_time)
                    if error_output:
                        logger.debug("Gemini Stderr: %s", error_output[:500])

                    time.sleep(wait_time)
                    continue

                return output

            except subprocess.TimeoutExpired:
                retry_count += 1
                logger.error("Gemini CLI killed after timeout. Retry %d/%d...", retry_count, max_retries)
                time.sleep(60)
                continue

        # CLI failed 3 times — fall back to SDK for reliability
        logger.warning("Gemini CLI failed %d times. Falling back to SDK...", max_retries)
        return self._run_gemini_sdk(prompt, timeout)

    def _run_gemini_sdk(self, prompt: str, timeout: int) -> str:
        """Fallback: call Gemini 3.1 Pro directly via google-genai SDK.

        Avoids stdin piping and CLI overhead entirely. Uses the same Pro Ultra
        subscription as Deep Think, just without extended thinking.
        """
        logger.info("Running Gemini SDK fallback — %d chars prompt", len(prompt))

        if self._deep_think_client is None:
            from google import genai
            if not self.config.google_api_key:
                raise RunnerError(
                    "google_api_key required for Gemini SDK fallback. "
                    "Set GOOGLE_API_KEY env var or edit forge/config.py"
                )
            self._deep_think_client = genai.Client(api_key=self.config.google_api_key)

        from google.genai import types

        response_holder: list = []
        error_holder: list = []
        call_done = threading.Event()

        def _sdk_call():
            try:
                resp = self._deep_think_client.models.generate_content(
                    model=self.config.gemini_model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        http_options=types.HttpOptions(timeout=timeout * 1000),
                    ),
                )
                response_holder.append(resp)
            except Exception as e:
                error_holder.append(e)
            finally:
                call_done.set()

        start = time.time()
        thread = threading.Thread(target=_sdk_call, daemon=True)
        thread.start()

        last_log = start
        while not call_done.is_set():
            elapsed = time.time() - start
            if elapsed - (last_log - start) >= 30:
                logger.info("[Gemini/SDK] Still running... %.0fs elapsed", elapsed)
                last_log = time.time()
            call_done.wait(timeout=5)

        elapsed = time.time() - start

        if error_holder:
            raise RunnerError(f"Gemini SDK failed after {elapsed:.0f}s: {error_holder[0]}")

        response = response_holder[0]
        try:
            text = response.text or "(No response generated)"
        except ValueError:
            text = "(Response blocked by safety filters)"

        logger.info("[Gemini/SDK] Completed in %.1fs (%d chars output)", elapsed, len(text))
        return text

    # ── Claude Code CLI ───────────────────────────────────────────────────

    def run_claude(
        self,
        prompt: str,
        timeout: int | None = None,
        needs_filesystem: bool = True,
        blueprint: str = "full",
    ) -> str:
        """Run Claude Code via CLI. Max subscription — no cost.

        Args:
            prompt: The prompt text.
            timeout: Hard timeout in seconds (default: config.claude_timeout).
            needs_filesystem: If True, run in target project dir so Claude can
                edit files.  If False, run in a neutral dir to avoid the CLI
                auto-loading a huge project into its context.
            blueprint: Intelligence level — "full" for implementation stages,
                "compact" for review/evaluation, "none" to skip.
        """
        if timeout is None:
            timeout = self.config.claude_timeout

        if self._claude_bin is None:
            self._claude_bin = self.config.resolve_claude_cli()

        import tempfile
        work_dir = str(self.config.target_project) if needs_filesystem else tempfile.gettempdir()

        # Inject Worker Intelligence Blueprint — teaches Claude self-verification,
        # tool usage, quality standards, and pipeline awareness.
        if self.config.worker_intelligence and blueprint != "none":
            from forge.worker_blueprint import WORKER_BLUEPRINT, WORKER_BLUEPRINT_COMPACT
            preamble = WORKER_BLUEPRINT if blueprint == "full" else WORKER_BLUEPRINT_COMPACT
            prompt = preamble + prompt

        logger.info("Running Claude Code CLI — %d chars prompt (cwd=%s, blueprint=%s)",
                     len(prompt), "project" if needs_filesystem else "neutral", blueprint)

        cmd = [
            self._claude_bin,
            "--print",
            "--output-format", "text",
            "--model", self.config.claude_model,
            "--dangerously-skip-permissions",
            "--verbose",
        ]

        max_retries = 5
        retry_count = 0

        while retry_count < max_retries:
            try:
                # Always pipe prompt via stdin — avoids Windows command-line
                # length limits and quoting issues, especially under nohup.
                result = self._run_monitored(
                    cmd,
                    input_text=prompt,
                    timeout=timeout,
                    stage_name="Claude/Opus",
                    expected_min=30,
                    expected_max=300,   # 5 min expected max
                    stall_limit=180,    # 3 min past expected = stall warning
                    cwd=work_dir,
                )

                # If stdin piping failed, fall back to writing prompt to a
                # temp file and telling Claude to read it.
                if result.returncode != 0 and "Input must be provided" in result.stderr:
                    logger.warning("Claude stdin piping failed, trying temp file reference")
                    prompt_file = self.config.forge_data_dir / "_temp_claude_prompt.txt"
                    prompt_file.parent.mkdir(parents=True, exist_ok=True)
                    prompt_file.write_text(prompt, encoding="utf-8")

                    file_prompt = f"Please read the instructions in {prompt_file} and execute them."
                    current_cmd = cmd + [file_prompt]
                    result = self._run_monitored(
                        current_cmd,
                        timeout=timeout,
                        stage_name="Claude/Opus (file fallback)",
                        expected_min=30,
                        expected_max=300,
                        stall_limit=180,
                        cwd=work_dir,
                    )

                if result.returncode != 0:
                    if not result.stdout.strip():
                        retry_count += 1
                        logger.error(
                            "Claude CLI failed (exit %d): %s. Retry %d/%d in 60s...",
                            result.returncode, (result.stderr or result.stdout)[:500],
                            retry_count, max_retries,
                        )
                        time.sleep(60)
                        continue
                    logger.warning(
                        "Claude CLI returned non-zero (%d) but produced output — using it",
                        result.returncode,
                    )
                break
            except subprocess.TimeoutExpired:
                retry_count += 1
                logger.error(
                    "Claude CLI killed after timeout. Retry %d/%d in 60s...",
                    retry_count, max_retries,
                )
                time.sleep(60)
                continue

        if retry_count >= max_retries:
            raise RunnerError(
                f"Claude CLI failed after {max_retries} retries",
                stdout="", stderr="Exhausted all retries",
            )

        return result.stdout

    # ── Deep Think (google-genai SDK) ─────────────────────────────────────

    def run_deep_think(self, prompt: str, system: str = "", timeout: int | None = None) -> str:
        """Run Gemini Deep Think via google-genai SDK. Pro Ultra — no cost.

        Uses ThinkingLevel.HIGH for extended reasoning chains.
        Best with detailed 1,000-3,000 word prompts crafted by Claude.

        Runs the SDK call in a background thread with active elapsed-time
        monitoring so a hung call doesn't silently block the pipeline.
        """
        if timeout is None:
            timeout = self.config.deep_think_timeout

        logger.info("Running Deep Think — %d chars prompt (timeout %ds)", len(prompt), timeout)

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

        # Run SDK call in a thread so we can monitor elapsed time
        response_holder: list = []
        error_holder: list = []
        call_done = threading.Event()

        def _sdk_call():
            try:
                resp = self._deep_think_client.models.generate_content(
                    model=self.config.deep_think_model,
                    contents=prompt,
                    config=types.GenerateContentConfig(**config_kwargs),
                )
                response_holder.append(resp)
            except Exception as e:
                error_holder.append(e)
            finally:
                call_done.set()

        expected_max = 300  # 5 min expected max for Deep Think
        max_retries = 3

        for attempt in range(1, max_retries + 1):
            start = time.time()
            response_holder.clear()
            error_holder.clear()
            call_done.clear()

            thread = threading.Thread(target=_sdk_call, daemon=True)
            thread.start()

            warned_slow = False
            last_log = start

            while not call_done.is_set():
                elapsed = time.time() - start

                # Status log every 30 seconds
                if elapsed - (last_log - start) >= 30:
                    logger.info("[Deep Think] Still running... %.0fs elapsed", elapsed)
                    last_log = time.time()

                # Warning if exceeding expected duration
                if elapsed > expected_max and not warned_slow:
                    logger.warning(
                        "[Deep Think] SLOW — %.0fs elapsed (expected <%ds). "
                        "SDK has its own timeout at %ds.",
                        elapsed, expected_max, timeout,
                    )
                    warned_slow = True

                call_done.wait(timeout=5)  # Poll every 5 seconds

            elapsed = time.time() - start

            if error_holder:
                err = error_holder[0]
                logger.error(
                    "[Deep Think] Failed after %.0fs: %s. %s",
                    elapsed, err,
                    f"Retrying ({attempt}/{max_retries}) in 60s..." if attempt < max_retries
                    else "No more retries.",
                )
                if attempt < max_retries:
                    time.sleep(60)
                    continue
                raise RunnerError(f"Deep Think failed after {max_retries} retries: {err}")

            response = response_holder[0]

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
                "[Deep Think] Completed in %.1fs (thinking: %s)",
                elapsed,
                "HIGH" if thinking_used else "standard",
            )

            try:
                return response.text or "(No response generated)"
            except ValueError:
                return "(Response blocked by safety filters)"

        # Should not reach here, but just in case
        raise RunnerError("Deep Think failed: exhausted all retries")

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
        return self.run_claude(meta_prompt, timeout=300, needs_filesystem=False, blueprint="compact")
