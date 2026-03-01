# Phase 2: Make ChromaDB Memory & Cortex Layer Fully Proprietary

## OBJECTIVE
Replace ChromaDB's implicit default embedding model with an explicit, configurable
embedding function using direct SDK calls (google-genai or sentence-transformers).
This eliminates the last transitive LangChain dependency and gives us full control
over the embedding pipeline.

## SCOPE — FILES TO MODIFY

### Memory Layer (memory/)
| File | What Changes |
|------|-------------|
| `memory/chromadb_store.py` | Add explicit `embedding_function` parameter to collection creation. Create a custom `EmbeddingFunction` class that wraps our chosen model. |
| `memory/manager.py` | Pass embedding config through to ChromaMemoryStore. |
| `memory/recall.py` | No direct ChromaDB changes needed — it calls chromadb_store.query(). |
| `memory/journal.py` | No direct ChromaDB changes needed — it calls chromadb_store.store(). |

### Cortex Layer (cortex/)
| File | What Changes |
|------|-------------|
| `cortex/rule_store.py` | Same pattern: add explicit `embedding_function` to collection creation. Share the same custom embedding class. |
| `cortex/golden_store.py` | Same pattern: add explicit `embedding_function` to collection creation. |
| `cortex/manager.py` | Pass embedding config through to rule_store and golden_store. |

### Config
| File | What Changes |
|------|-------------|
| `config/settings.py` | Add `embedding_model` and `embedding_provider` settings. |
| `pyproject.toml` | Update LangChain ban comments (it's now fully gone, not transitive). Pin `sentence-transformers` if using local embeddings. |

### New File
| File | Purpose |
|------|---------|
| `models/embedding.py` | Proprietary `SpectreEmbeddingFunction` implementing ChromaDB's `EmbeddingFunction` protocol. Supports multiple backends: Gemini (`text-embedding-004`), local SentenceTransformers, or OpenAI-compatible. |

### Tests
| File | What Changes |
|------|-------------|
| `tests/test_chromadb_store.py` | Update to verify custom embedding function is used. |
| `tests/test_cortex_rule_store.py` | Same: verify custom embedding function. |
| `tests/test_embedding.py` | NEW — unit tests for `SpectreEmbeddingFunction`. |

## DO NOT TOUCH (sacred top-layer files)
- `personality/` — All files
- `context/` — compiler.py, scheduler.py, snapshot.py
- `gateway/` — server.py, channels/
- `tools/` — All files EXCEPT registry.py
- `config/routes.py` — Router rules
- `core/agent.py` — The state machine (already refactored in Phase 1)
- `core/router.py` — The keyword router
- `core/prompt_assembler.py` — The prompt builder

## IMPLEMENTATION PLAN

### Step 1: Create `models/embedding.py`
Create a proprietary embedding function that implements ChromaDB's `EmbeddingFunction` protocol.

```python
"""Proprietary embedding function for ChromaDB collections.

Replaces ChromaDB's implicit default embedding model with explicit,
configurable embedding via direct SDK calls. Zero LangChain dependency.
"""
from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from chromadb import Documents, EmbeddingFunction, Embeddings

logger = logging.getLogger(__name__)


class SpectreEmbeddingFunction(EmbeddingFunction):
    """ChromaDB-compatible embedding function using Gemini text-embedding-004.

    Falls back to a local SentenceTransformers model if Gemini is unavailable.
    """

    def __init__(self, provider: str = "gemini", model: str | None = None):
        self._provider = provider
        if provider == "gemini":
            self._model = model or "text-embedding-004"
            self._embed = self._embed_gemini
        elif provider == "local":
            self._model = model or "all-MiniLM-L6-v2"
            self._embed = self._embed_local
            self._st_model = None  # Lazy load
        else:
            raise ValueError(f"Unknown embedding provider: {provider}")

    def __call__(self, input: Documents) -> Embeddings:
        return self._embed(input)

    def _embed_gemini(self, texts: Documents) -> Embeddings:
        """Embed using Google's text-embedding-004 via google-genai SDK."""
        from google import genai
        client = genai.Client()
        # Batch embed (API supports up to 2048 texts per call)
        result = client.models.embed_content(
            model=self._model,
            contents=texts,
        )
        return [list(e.values) for e in result.embeddings]

    def _embed_local(self, texts: Documents) -> Embeddings:
        """Embed using local SentenceTransformers model (offline fallback)."""
        if self._st_model is None:
            from sentence_transformers import SentenceTransformer
            self._st_model = SentenceTransformer(self._model)
        embeddings = self._st_model.encode(texts, show_progress_bar=False)
        return embeddings.tolist()
```

### Step 2: Add settings to `config/settings.py`
Add two new settings:
```python
embedding_provider: str = "gemini"       # "gemini" or "local"
embedding_model: str = "text-embedding-004"  # Model ID for the provider
```

### Step 3: Update `memory/chromadb_store.py`
- Import `SpectreEmbeddingFunction` from `models.embedding`
- Accept `embedding_function` parameter in `__init__`
- Pass it to `get_or_create_collection()`
- If no embedding_function provided, create one from settings

### Step 4: Update `cortex/rule_store.py`
- Same pattern as Step 3
- Import and use `SpectreEmbeddingFunction`
- Pass to `get_or_create_collection()`

### Step 5: Update `cortex/golden_store.py`
- Same pattern as Steps 3-4

### Step 6: Update managers
- `memory/manager.py`: Create `SpectreEmbeddingFunction` once, pass to ChromaMemoryStore
- `cortex/manager.py`: Create `SpectreEmbeddingFunction` once, pass to CortexRuleStore and GoldenExampleStore

### Step 7: Update `pyproject.toml`
- Update the LangChain ban message to say "fully removed" not "transitive dep"
- Add `google-genai` to deps if not already present (should already be there from Phase 1)
- Optionally add `sentence-transformers` as an optional dependency group

### Step 8: Write tests
- Test `SpectreEmbeddingFunction` with mock Gemini API
- Test that ChromaMemoryStore uses custom embedding
- Test that CortexRuleStore uses custom embedding
- Test fallback from Gemini to local if Gemini fails

## CONSTRAINTS
1. **Backward compatibility**: Existing ChromaDB databases must still work. ChromaDB re-embeds on query if the embedding function changes, so existing data persists but gets re-embedded on first query.
2. **No API key requirement for tests**: Tests must mock the embedding calls. Real embedding calls only happen at runtime.
3. **Preserve all ChromaDB API patterns**: `upsert()`, `query()`, `get()`, `delete()`, `count()` — these are direct ChromaDB API calls and should remain as-is. Only the `embedding_function` parameter changes.
4. **Token budget unchanged**: Embedding happens at storage/retrieval time, not at prompt assembly time. The 2,000-token memory budget is unaffected.
5. **All 397+ existing tests must continue to pass** with zero regressions.

## WHAT SUCCESS LOOKS LIKE
- `grep -r "langchain" spectre/` returns ZERO hits (not even transitive comments)
- All ChromaDB collections use `SpectreEmbeddingFunction` explicitly
- `models/embedding.py` exists with full implementation
- Embedding provider is configurable via `config/settings.py`
- All tests pass (existing + new)
- No performance regression (embedding calls are batched, not per-document)

## RISK NOTES
- ChromaDB's `EmbeddingFunction` protocol changed between v0.4 and v0.5. We pin `chromadb>=0.5.0` so this is stable.
- Gemini's `text-embedding-004` returns 768-dimensional vectors. If the ChromaDB collection was previously created with a different dimension (ChromaDB default uses 384-dim all-MiniLM-L6-v2), the collection must be recreated. The `clear()` method already handles this (drop + recreate). Add a migration note.
- The `google-genai` SDK's `embed_content` API is synchronous. Since ChromaDB's `EmbeddingFunction.__call__` is also synchronous, this is fine. No async wrapper needed.
