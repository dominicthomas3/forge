Phase 5: Gateway Layer — Hardening, Testing, and Event Queue Audit

Target: C:\Users\domin\spectre — gateway/ layer (10 files, 6,107 lines)

This is the TRANSMISSION of Spectre. The core engine (core/, memory/, cortex/) has been hardened in Phases 1-4. Now we harden the layer that connects Spectre to the outside world: WebSocket server, Telegram, WhatsApp, Twitter, Email, the Event Queue, Agent Monitor, and Protocol framing.

CRITICAL CONTEXT: The Event Queue (gateway/event_queue.py) and its Telegram integration were written MANUALLY in a single session — they have NEVER been through the Forge convergence loop. The parallel pipeline in core/agent.py (asyncio.gather) was also manual. These are the highest-risk code paths.

TARGET FILES (audit, harden, and write tests for ALL):
1. gateway/server.py (1,331 lines) — WebSocket server, auth, streaming, tick loop, channel orchestration
2. gateway/protocol.py (281 lines) — Frame parsing, factory functions for all message types
3. gateway/event_queue.py (299 lines) — Adaptive message buffering, per-user queues, debounce/drain logic
4. gateway/agent_monitor.py (419 lines) — Background agent polling, mutation claim detection, anti-hallucination
5. gateway/channels/telegram.py (1,283 lines) — Telegram Bot API, Event Queue integration, photo/video/swarm
6. gateway/channels/whatsapp.py (851 lines) — Meta Cloud API, webhook HMAC, 24h session tracking
7. gateway/channels/twitter.py (1,110 lines) — Polling mentions/DMs, tweet threading, rate limits
8. gateway/channels/email.py (525 lines) — Gmail IMAP/SMTP, dual-account, flood prevention

ENGINEERING ONLY — DO NOT TOUCH:
- personality/ files (identity — sacred)
- memory/ files (Phase 2 converged — 541 tests)
- cortex/ files (Phase 2 converged)
- core/agent.py, core/router.py, core/prompt_assembler.py (Phase 4 converged — 609 tests)
- core/response_handler.py, core/reminder_manager.py (Phase 4 converged)
- tools/ files (separate scope, do not modify)
- config/settings.py (do not modify unless adding gateway-specific settings)
- Prompt text content, personality text, tool descriptions
- models/ files

WHAT TO AUDIT AND HARDEN:

1. EVENT QUEUE (gateway/event_queue.py) — HIGHEST PRIORITY:
   - Race conditions in _drain_loop: what if push() and _collect_pending() overlap?
   - Debounce timer correctness: does _reset_debounce properly cancel previous timers?
   - Merge logic: does _merge_events handle edge cases (single event, metadata conflicts, empty text)?
   - Overflow: does drop-oldest work correctly when queue is full?
   - Shutdown: are all resources cleaned up? Timer handles cancelled? Tasks awaited?
   - Thread safety: is the queue safe across concurrent async tasks?

2. TELEGRAM EVENT QUEUE INTEGRATION (gateway/channels/telegram.py):
   - Photo/video/swarm messages all route through Event Queue — verify correctness
   - The old _process_and_respond method was REMOVED — verify no dead references remain
   - _handle_queued_message: typing indicator loop, timeout handling, empty response guard
   - Fallback paths (elif handler is not None): are they correct if event_queue is None?
   - Proactive messaging: does it interact safely with the Event Queue?

3. WHATSAPP (gateway/channels/whatsapp.py):
   - Still uses old asyncio.Lock() processing pattern — consider Event Queue migration
   - Webhook HMAC validation: if whatsapp_app_secret is empty, does validation fail open? (security concern)
   - Message dedup: is the TTL cache thread-safe?
   - 24-hour session window: edge cases around window expiry

4. SERVER (gateway/server.py):
   - WebSocket connection lifecycle: auth, heartbeat, cleanup on disconnect
   - chat.send cancel-and-restart: race conditions when cancelling active streams
   - Tick loop (30s): concurrent access to connections dict
   - Channel init error handling: does one channel failure prevent others from starting?
   - Graceful shutdown: are all connections properly closed?

5. AGENT MONITOR (gateway/agent_monitor.py):
   - Mutation claim regex: false positives/negatives in detecting file changes
   - Verification flags: are they properly cleared after verification?
   - Pending results buffer: is it properly bounded?

6. PROTOCOL (gateway/protocol.py):
   - Frame parsing: malformed JSON, missing fields, type mismatches
   - Factory functions: do all make_* functions produce valid frames?

7. TWITTER (gateway/channels/twitter.py):
   - State file persistence: corruption on crash during write
   - Rate limit handling: does it back off properly?
   - Tweet threading: edge cases in sentence-boundary splitting at 280 chars

8. EMAIL (gateway/channels/email.py):
   - IMAP/SMTP via asyncio.to_thread: timeout handling, connection cleanup
   - Flood prevention: first-start mark-as-seen logic
   - HTML stripping: edge cases in body extraction
   - SSL context: is it properly configured?

9. TESTS — WRITE COMPREHENSIVE UNIT TESTS:
   - test_event_queue.py — drain logic, merge, debounce, overflow, shutdown
   - test_protocol.py — frame parsing, factory functions, edge cases
   - test_agent_monitor.py — mutation regex, verification flags
   - test_gateway_server.py — connection lifecycle, auth, channel management
   - test_telegram_channel.py — event queue routing, message handling
   - test_whatsapp_channel.py — webhook validation, dedup, session windows
   - test_email_channel.py — IMAP parsing, flood prevention, HTML stripping

All 609+ existing tests must pass. DO NOT reduce test coverage.
Test count should INCREASE significantly (targeting 700+).
