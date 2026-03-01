# Phase 2: Lamborghini Memory Architecture — Full Proprietary Overhaul

## OBJECTIVE
Replace the entire ChromaDB + default embedding memory stack with a world-class
"Cognitive Tri-Store" architecture:

1. **LanceDB** replaces ChromaDB (faster, hybrid search, columnar storage)
2. **Gemini Embedding** (`gemini-embedding-001`) replaces default embeddings (#1 on MTEB at 68.32)
3. **Hybrid Search** (vector + BM25 keyword) replaces pure vector search (+26% recall)
4. **Memory Consolidation** pipeline for intelligent compression (26% better responses, 90% fewer tokens)
5. **Knowledge Graph** (NetworkX) for relationship-based memory (+35 points precision over flat vector search)

This is NOT a code swap — this is an architectural upgrade. Every component must
be production-quality, thoroughly tested, and demonstrably better than what it replaces.

---

## SCOPE — WHAT TO BUILD

### NEW FILES TO CREATE

#### 1. `models/embedding.py` — Proprietary Embedding Function
A LanceDB-compatible embedding function using Gemini's `gemini-embedding-001` model
(#1 ranked on MTEB, 768 dimensions, free with Google AI subscription).

Requirements:
- Use `google.genai` SDK (`client.models.embed_content()`)
- Lazy-load the `genai.Client` (don't crash on import if no API key)
- Thread-safe initialization with `threading.Lock`
- Batch chunking (max 100 documents per API call to avoid HTTP 413)
- Handle empty input lists (return `[]` immediately)
- Defensive string casting: `[str(doc) for doc in input if doc]`
- Retry on transient 5xx errors (use `tenacity` or simple retry loop)
- Local fallback to `sentence-transformers` `all-MiniLM-L6-v2` (384-dim) if configured
- Factory function `create_embedding_function(provider, model)` for clean instantiation

#### 2. `memory/lancedb_store.py` — LanceDB Memory Store (replaces chromadb_store.py)
A LanceDB-backed vector + full-text hybrid store for long-term semantic memory.

Requirements:
- Use `lancedb` Python package (embedded, no server)
- Define a PyArrow schema: `id` (string), `text` (string), `vector` (fixed-size-list of float32, 768-dim), `timestamp` (float64), `metadata` (string/JSON), `type` (string: "exchange" | "journal" | "summary")
- **Hybrid search**: combine vector similarity + BM25 full-text search using LanceDB's native `create_fts_index()` and `hybrid_search()`
- Reciprocal Rank Fusion (RRF) to merge vector and keyword results
- Same public API as ChromaMemoryStore: `store()`, `query()`, `count()`, `clear()`, `close()`
- Add `hybrid_query(text, top_k)` that returns results from both vector AND keyword search
- Persist to `memory/data/lance_db/` directory
- Ebbinghaus-style decay: multiply relevance by `exp(-0.693 * days_old / 30)` (30-day half-life)

#### 3. `memory/consolidation.py` — Memory Consolidation Pipeline
An async pipeline that compresses raw memories into higher-level abstractions.

Requirements:
- `consolidate_daily(memories: list[str]) -> str` — Takes a day's raw exchanges, sends to Gemini Flash for compression into a ~200-token summary
- `consolidate_weekly(daily_summaries: list[str]) -> str` — Compresses 7 daily summaries into themes and patterns
- `extract_facts(text: str) -> list[dict]` — Uses Gemini Flash to extract structured facts (entity, relationship, value) from conversations. Example: `{"entity": "Dominic", "relationship": "prefers", "value": "analogies over code"}`
- `should_consolidate(store) -> bool` — Returns True if there are >25 unconsolidated raw exchanges
- Called by MemoryManager during journal flush or on a schedule

#### 4. `memory/knowledge_graph.py` — Relationship-Based Knowledge Graph
A NetworkX-backed knowledge graph that stores entities and relationships extracted
from conversations. This gives Spectre structured understanding — not just "similar text"
but actual relationships like "Dominic → works on → Spectre" and "Forge → produces → clean code."

Requirements:
- Use `networkx` Python package (in-memory graph, serialized to JSON on disk)
- `KnowledgeGraph` class with methods:
  - `add_entity(entity_id: str, entity_type: str, properties: dict)` — Add a node (person, project, concept, tool)
  - `add_relationship(source: str, target: str, relation: str, properties: dict)` — Add a directed edge
  - `query_neighbors(entity_id: str, depth: int = 1) -> list[dict]` — Get all entities within N hops
  - `query_relationships(query: str, top_k: int = 5) -> list[dict]` — Semantic search over relationship descriptions (embed relationship text in LanceDB for vector search)
  - `extract_from_conversation(user_msg: str, assistant_msg: str) -> list[tuple]` — Use Gemini Flash to extract (subject, predicate, object) triplets from a conversation
  - `save()` / `load()` — Serialize graph to/from JSON at `memory/data/knowledge_graph.json`
  - `get_context_for_prompt(query: str, max_tokens: int = 400) -> str` — Format relevant graph context as natural language for prompt injection
- Persist to `memory/data/knowledge_graph.json`
- Graph is updated incrementally (no full rebuild) after each conversation
- Integrate with MemoryManager: call `extract_from_conversation()` in `process_exchange()` and inject graph context in `get_context_for_prompt()`
- The token budget for graph context is ~400 tokens (part of the existing ~2,000 token memory budget)

#### 5. `tests/test_knowledge_graph.py` — Knowledge Graph tests
- Test add/query entity and relationship operations
- Test neighbor traversal at depth 1 and depth 2
- Test extract_from_conversation with mocked Gemini Flash
- Test serialization/deserialization (save/load cycle)
- Test get_context_for_prompt stays within token budget
- Test empty graph edge cases

#### 6. `tests/test_lancedb_store.py` — Comprehensive LanceDB tests
- Test store/query/count/clear lifecycle
- Test hybrid search returns results from BOTH vector and keyword matches
- Test decay scoring (older memories score lower)
- Test empty database edge cases
- Test batch operations
- Mock the embedding function (don't hit real API in tests)

#### 5. `tests/test_embedding.py` — Embedding function tests
- Test Gemini embedding with mocked `google.genai` client
- Test batch chunking (>100 documents splits into batches)
- Test empty input handling
- Test thread-safe lazy initialization
- Test local fallback when provider="local"

#### 6. `tests/test_consolidation.py` — Consolidation pipeline tests
- Test daily compression with mocked Gemini Flash
- Test fact extraction output format
- Test weekly rollup

### FILES TO MODIFY

#### 7. `memory/manager.py` — Wire up new LanceDB store + consolidation + knowledge graph
- Replace `ChromaMemoryStore` import with `LanceDBMemoryStore`
- Create embedding function once in `__init__`, pass to store
- Initialize `KnowledgeGraph` in `__init__`, load from disk
- In `process_exchange()`: call `knowledge_graph.extract_from_conversation()` to incrementally update the graph
- In `get_context_for_prompt()`: include graph context alongside vector recall (allocate ~400 tokens for graph, ~300 for vector recall within the 2,000 token budget)
- Add consolidation trigger in `process_exchange()` or journal flush
- Save knowledge graph to disk after updates
- Keep the same external API so `prompt_assembler.py` doesn't change

#### 8. `memory/recall.py` — Use hybrid search
- Update to call `hybrid_query()` instead of plain `query()`
- The Gemini Flash synthesis step stays the same

#### 9. `memory/journal.py` — Use LanceDB for journal vectorization
- Replace ChromaDB store calls with LanceDB store calls
- Add consolidation trigger after journal flush

#### 10. `cortex/rule_store.py` — Migrate to LanceDB
- Replace `chromadb.PersistentClient` with LanceDB
- Keep the same composite scoring logic (sim*0.70 + conf*0.25 + recency*0.05)
- Add hybrid search for rule retrieval (keyword + vector)
- Use versioned table name (`spectre_cortex_rules_v2`) to avoid migration conflicts
- Persist to `cortex/data/lance_db/`

#### 11. `cortex/golden_store.py` — Migrate to LanceDB
- Same pattern as rule_store.py migration
- Replace ChromaDB with LanceDB
- Use versioned table name (`spectre_cortex_golden_v2`)

#### 12. `cortex/manager.py` — Wire up new stores
- Create embedding function once, pass to both stores
- Update imports

#### 13. `config/settings.py` — Add new settings
```python
embedding_provider: str = "gemini"
embedding_model: str = "gemini-embedding-001"
lance_db_path: str = "memory/data/lance_db"
cortex_lance_db_path: str = "cortex/data/lance_db"
hybrid_search_enabled: bool = True
hybrid_vector_weight: float = 0.7  # Weight for vector results in RRF
hybrid_keyword_weight: float = 0.3  # Weight for BM25 results in RRF
memory_decay_half_life_days: float = 30.0  # Ebbinghaus half-life
consolidation_threshold: int = 25  # Raw exchanges before auto-consolidation
knowledge_graph_enabled: bool = True
knowledge_graph_path: str = "memory/data/knowledge_graph.json"
knowledge_graph_max_tokens: int = 400  # Token budget for graph context in prompt
```

#### 14. `pyproject.toml` — Update dependencies
- Add `lancedb>=0.15.0` to dependencies
- Add `tantivy` (required by LanceDB for full-text search)
- Add `networkx>=3.0` to dependencies (for knowledge graph)
- Update LangChain ban message to "fully removed"
- Remove `"memory/**" = ["TID251"]` exception from per-file-ignores
- Keep `chromadb` in deps for now (cortex might still reference during migration)

### FILES TO DELETE (after migration verified)
- `memory/chromadb_store.py` — Replaced by `memory/lancedb_store.py`

---

## DO NOT TOUCH (sacred files)
- `personality/` — All files
- `context/` — compiler.py, scheduler.py, snapshot.py
- `gateway/` — server.py, channels/
- `tools/` — All files EXCEPT registry.py
- `config/routes.py` — Router rules
- `core/agent.py` — The state machine
- `core/router.py` — The keyword router
- `core/prompt_assembler.py` — The prompt builder (its API doesn't change)

---

## IMPLEMENTATION ORDER (Critical — follow exactly)

1. **config/settings.py** — Add all new settings first
2. **models/embedding.py** — The embedding function (no dependencies on other new code)
3. **tests/test_embedding.py** — Verify embedding works before building on it
4. **memory/lancedb_store.py** — The new store (depends on embedding.py)
5. **tests/test_lancedb_store.py** — Verify store works
6. **memory/consolidation.py** — The consolidation pipeline
7. **tests/test_consolidation.py** — Verify consolidation
8. **memory/knowledge_graph.py** — The knowledge graph
9. **tests/test_knowledge_graph.py** — Verify knowledge graph
10. **memory/manager.py** — Wire everything together (LanceDB + consolidation + knowledge graph)
9. **memory/recall.py** — Update to hybrid search
10. **memory/journal.py** — Update to LanceDB
11. **cortex/rule_store.py** — Migrate to LanceDB
12. **cortex/golden_store.py** — Migrate to LanceDB
13. **cortex/manager.py** — Wire up cortex stores
14. **pyproject.toml** — Update deps and linting
15. Run ALL tests — existing + new must pass

---

## CONSTRAINTS

1. **No data loss**: Use versioned table names (e.g., `spectre_memory_v2`) so old ChromaDB data remains intact. Don't delete old databases.
2. **Tests must mock API calls**: No real Gemini API calls in tests. Mock `google.genai`.
3. **Thread safety**: LanceDB handles concurrent access, but the embedding function must use `threading.Lock` for lazy init.
4. **Token budget unchanged**: The 2,000-token memory budget in prompt_assembler is NOT changing. Consolidation happens at storage time, not retrieval time.
5. **All 397+ existing tests must pass** with zero regressions.
6. **Public API preserved**: `MemoryManager`, `CortexManager`, and their methods keep the same signatures. Only internal implementation changes.

---

## WHAT SUCCESS LOOKS LIKE

- `grep -r "chromadb" spectre/ --include="*.py"` returns ZERO hits (except possibly test mocks)
- `grep -r "langchain" spectre/` returns ZERO hits
- All memory/cortex stores use LanceDB with Gemini Embedding
- Hybrid search (vector + BM25) is enabled by default
- Memory consolidation pipeline exists and is triggered automatically
- Ebbinghaus decay weights older memories lower
- All tests pass (existing + new)
- `pip install lancedb tantivy networkx` are the only new dependencies
- Knowledge graph extracts entities/relationships from conversations via Gemini Flash
- Graph context is injected into prompts alongside vector recall (~400 tokens)

---

## RISK NOTES

- LanceDB's Python API may differ from ChromaDB's. Read the LanceDB docs carefully — it uses PyArrow tables, not collection objects.
- LanceDB's `create_fts_index()` must be called AFTER data is inserted (can't create FTS index on empty table). Handle this gracefully.
- Gemini Embedding returns 768-dim vectors. Schema must match.
- `tantivy` is a Rust-backed library. It should install fine on Windows via pip but verify.
- The consolidation pipeline uses Gemini Flash, which is already in the codebase (`models/gemini_flash.py`). Reuse the existing model wrapper, don't create a new one.
