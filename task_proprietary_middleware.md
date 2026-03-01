# FORGE DIRECTIVE: 100% Proprietary, Zero-Dependency Middleware (Phase 1)

## THE VISION
Spectre's current middle layer is heavily reliant on LangChain and LangGraph. Our ultimate goal is a "Ferrari" architecture with zero framework abstractions. 

**CRITICAL SCOPE LIMIT:** This is **PHASE 1**. You are ONLY ripping out LangChain and LangGraph. You MUST KEEP the official `anthropic` and `google-genai` SDKs for now as a stepping stone. Do not rewrite the raw HTTP/REST logic yet. 

The goal for this Phase 1 iteration is to replace all LangChain Message classes with plain Python dicts, replace LangGraph with a lean async state machine, and replace the `@tool` decorator schema bindings with our own custom generator, while continuing to pass those schemas/dicts to the official `ChatAnthropic` and `ChatGoogleGenerativeAI` client configurations.

---

## YOUR SPECIFIC ROLES & THE EVOLUTION LOOP
You are an elite team. You will read this recursively. 

### 1. JIM (Gemini 3.1 Pro - 1M Context Window) — The Architect
- **Your Job:** You brainstorm and map the Phase 1 architecture. How do we cleanly lift LangChain out of the 13 middle layer files without breaking the top layer logic? 

### 2. DEEP THINK (Gemini 3.1 Pro with High Thinking) — Stress Tester
- **Your Job:** You take Jim's plan and tear it apart. Validate that NO business logic (cortex, memory, tools) is touched. Ensure we didn't accidentally try to remove the official API SDKs (which is reserved for Phase 2). 

### 3. CLAUDE CODE (Opus 4.6) — The Master Craftsman
- **Your Job:** You edit the 13 files. Replace LangGraph elements in `core/agent.py`, update `models/claude_base.py` and `models/gemini_pro.py` to use plain Python dicts with the Official SDKs. Remove `langchain` and `langgraph` from `requirements.txt`. Do it flawlessly.

---

## THE CORE DIRECTIVES FOR PHASE 1

1.  **ERADICATE LANGGRAPH:**
    - Eradicate LangGraph completely from `core/agent.py`.
    - Build a custom, highly efficient async state loop tailored specifically to Spectre's exact pipeline (Route -> Assemble Prompt -> Execute API -> Tool Loop -> Response). 

2.  **LIGHTWEIGHT MESSAGE CLASSES (NO LANGCHAIN):**
    - Remove `HumanMessage`, `AIMessage`, `ToolMessage`, `SystemMessage`, etc.
    - Replace them with standard Python dicts that the official Anthropic and Google SDKs accept directly.

3.  **PROPRIETARY TOOL BINDING:**
    - Remove all LangChain `BaseTool` / `@tool` decorators from `tools/registry.py` and `core/tool_binder.py`.
    - Build a custom schema generation system that seamlessly inspects our functions and perfectly maps them to the structured tool JSON expected by the official SDKs. 

4.  **RETAIN OFFICIAL SDKS (FOR NOW):**
    - You MUST KEEP `anthropic` and `google-genai` imports.
    - DO NOT attempt to write direct `aiohttp` REST payload layers. That is Phase 2.

## CONSTRAINTS & SUCCESS METRICS
- **Business Logic is Sacred:** Do NOT touch the top end — memory (ChromaDB), rules, personality logic, gateway, or business logic tools.
- **Strict Token Audit:** Deep Think and Claude MUST include a full audit process over the generated token payload. Ensure Claude does not introduce "token bloat" for no reason. Minimize prompt templates and payloads tightly.
- **Anti-Gravity Operator Context:** The Anti-Gravity Agent (the top-level operator running this Forge loop) is monitoring this process. The Forge operates after `Morpheus` training sessions to continuously refine the codebase.
- **Goal:** When requirements.txt is purged of `langchain`, `langchain-core`, `langchain-anthropic`, `langchain-google-genai`, and `langgraph`, and the stress tests pass cleanly.