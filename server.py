# Copyright 2026 Eric Fogleman
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
server.py
Index local markdown manuals into Chroma and serve them over MCP.

Directory layout:
- Each manual lives in its own top-level folder.
- Each manual folder must be flat.
- Each manual folder must contain exactly one `.md` file.
- The folder name is the manual name.

Usage:
- Index with OpenAI embeddings:
  `EMBEDDING_PROVIDER=openai OPENAI_API_KEY=... python3 server.py index`
- Index with Ollama embeddings:
  `EMBEDDING_PROVIDER=ollama python3 server.py index`
- Run the MCP server:
  `python3 server.py`

MCP tools:
- `list_reference_manuals()`
  - Lists indexed manuals available for the active provider.
- `search_reference_manuals(manual_name, query, limit=5)`
  - Searches an indexed manual.
  - `manual_name` may be either an exact manual folder name or a close human-friendly name.
  - Example exact call: `search_reference_manuals("ngspice-manual-4p6", "measure period syntax", 5)`
  - Example fuzzy call: `search_reference_manuals("ngspice", "measure period syntax", 5)`

Optional environment variables:
- `OLLAMA_EMBED_MODEL`
- `EMBED_BATCH_SIZE`
- `MAX_SEARCH_LIMIT`
- `MAX_CHUNK_CHARS`
- `CHUNK_OVERLAP_CHARS`
- `DOCS_DIR`
- `DB_PATH`
- `ENABLE_TOOL_AUDIT_LOG` (`1`, `true`, `yes`, or `on` to enable JSONL tool audit logging)
- `TOOL_AUDIT_LOG_PATH`
"""
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from chromadb import PersistentClient
from chromadb.errors import NotFoundError
from mcp.server.fastmcp import FastMCP
from openai import OpenAI
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

# --- MULTI-PROVIDER CONFIGURATION ---
PROVIDER = os.environ.get("EMBEDDING_PROVIDER", "openai").lower()

if PROVIDER == "ollama":
    # Point to local Ollama server instance
    oai_client = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")
    EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")
    # Separate the collection name so vector spaces never clash
    DB_SUFFIX = "_ollama"
else:
    # Standard OpenAI setup
    oai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    EMBED_MODEL = "text-embedding-3-small"
    DB_SUFFIX = "_openai"

# --- CORE PATHS & CHROMA ---
CURRENT_DIR = Path(__file__).parent.resolve()


def env_flag_is_enabled(env_var_name: str) -> bool:
    value = os.environ.get(env_var_name, "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def resolve_path_from_env(env_var_name: str, default_path: Path) -> Path:
    raw_value = os.environ.get(env_var_name)
    if not raw_value:
        return default_path.resolve()
    return Path(raw_value).expanduser().resolve()


DOCS_DIR = resolve_path_from_env("DOCS_DIR", CURRENT_DIR)
DB_PATH = resolve_path_from_env("DB_PATH", CURRENT_DIR / ".central_vector_db")
TOOL_AUDIT_LOG_PATH = resolve_path_from_env("TOOL_AUDIT_LOG_PATH", CURRENT_DIR / "mcp_tool_audit.jsonl")

ENABLE_TOOL_AUDIT_LOG = env_flag_is_enabled("ENABLE_TOOL_AUDIT_LOG")

DEFAULT_EMBED_BATCH_SIZE = 32 if PROVIDER == "ollama" else 100
DEFAULT_MAX_CHUNK_CHARS = 1200 if PROVIDER == "ollama" else 4000
DEFAULT_CHUNK_OVERLAP_CHARS = 150 if PROVIDER == "ollama" else 400

EMBED_BATCH_SIZE = int(os.environ.get("EMBED_BATCH_SIZE", str(DEFAULT_EMBED_BATCH_SIZE)))
MAX_SEARCH_LIMIT = int(os.environ.get("MAX_SEARCH_LIMIT", "20"))
MAX_CHUNK_CHARS = int(os.environ.get("MAX_CHUNK_CHARS", str(DEFAULT_MAX_CHUNK_CHARS)))
CHUNK_OVERLAP_CHARS = int(os.environ.get("CHUNK_OVERLAP_CHARS", str(DEFAULT_CHUNK_OVERLAP_CHARS)))

chroma_client = PersistentClient(path=str(DB_PATH))
mcp = FastMCP(f"Multi-Manual Reference Engine ({PROVIDER.upper()})")


def build_collection_name(manual_name: str) -> str:
    base_name = manual_name[:-3] if manual_name.endswith(".md") else manual_name
    safe_name = base_name.replace(".", "_").replace(" ", "_").replace("-", "_")
    name_hash = hashlib.sha256(base_name.encode("utf-8")).hexdigest()[:12]
    return f"{safe_name}_{name_hash}{DB_SUFFIX}"


def get_excluded_dir_names() -> set[str]:
    db_dir_name = DB_PATH.name if DB_PATH.parent == DOCS_DIR else None
    excluded_dir_names = {
        ".git",
        ".venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        ".idea",
        ".vscode",
    }
    if db_dir_name:
        excluded_dir_names.add(db_dir_name)
    return excluded_dir_names


def get_available_manual_names() -> list[str]:
    """Return manual names valid for search_reference_manuals(manual_name=...)."""
    if not DOCS_DIR.exists() or not DOCS_DIR.is_dir():
        return []

    excluded_dir_names = get_excluded_dir_names()
    all_collection_names = {
        c if isinstance(c, str) else c.name
        for c in chroma_client.list_collections()
    }

    manual_names = []
    for manual_dir in DOCS_DIR.iterdir():
        if not manual_dir.is_dir():
            continue
        if manual_dir.name.startswith("."):
            continue
        if manual_dir.name in excluded_dir_names:
            continue

        subdirs = [p for p in manual_dir.iterdir() if p.is_dir()]
        if subdirs:
            continue

        markdown_files = [p for p in manual_dir.glob("*.md") if p.is_file()]
        if len(markdown_files) != 1:
            continue

        collection_name = build_collection_name(manual_dir.name)
        if collection_name in all_collection_names:
            manual_names.append(manual_dir.name)

    return sorted(manual_names)


def normalize_manual_name(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"\bmanual\b", "", value)
    value = re.sub(r"[_\-.]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def tokenize_manual_name(value: str) -> list[str]:
    normalized = normalize_manual_name(value)
    return [token for token in normalized.split(" ") if token]


def build_error_response(error_code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {
        "status": "error",
        "error_code": error_code,
        "message": message,
        **extra,
    }


def build_manual_not_found_error_response(
    requested_manual_name: str,
    available_manual_names: list[str],
) -> dict[str, Any]:
    return build_error_response(
        "manual_not_found",
        f"Manual '{requested_manual_name}' was not found.",
        manual_name_requested=requested_manual_name,
        available_manual_names=available_manual_names,
    )


def resolve_manual_name(
    requested_manual_name: str,
    available_manual_names: list[str],
) -> tuple[str | None, dict[str, Any] | None]:
    """
    Returns (resolved_manual_name, error_response).
    Exactly one of these values will be non-None.
    """
    if not available_manual_names:
        return None, build_manual_not_found_error_response(requested_manual_name, available_manual_names)

    if requested_manual_name in available_manual_names:
        return requested_manual_name, None

    requested_norm = normalize_manual_name(requested_manual_name)

    normalized_map: dict[str, list[str]] = {}
    for name in available_manual_names:
        normalized_map.setdefault(normalize_manual_name(name), []).append(name)

    exact_normalized_matches = normalized_map.get(requested_norm, [])
    if len(exact_normalized_matches) == 1:
        return exact_normalized_matches[0], None
    if len(exact_normalized_matches) > 1:
        return None, build_error_response(
            "manual_name_ambiguous",
            f"Manual name '{requested_manual_name}' is ambiguous.",
            manual_name_requested=requested_manual_name,
            matches=sorted(exact_normalized_matches),
        )

    requested_tokens = set(tokenize_manual_name(requested_manual_name))
    scored_matches: list[tuple[int, str]] = []

    for name in available_manual_names:
        name_norm = normalize_manual_name(name)
        name_tokens = set(tokenize_manual_name(name))
        score = 0

        if requested_norm and name_norm.startswith(requested_norm):
            score += 80
        elif requested_norm and requested_norm in name_norm:
            score += 60

        overlap = len(requested_tokens & name_tokens)
        score += overlap * 10

        if requested_tokens and requested_tokens.issubset(name_tokens):
            score += 25

        if score > 0:
            scored_matches.append((score, name))

    if not scored_matches:
        return None, build_manual_not_found_error_response(requested_manual_name, available_manual_names)

    scored_matches.sort(key=lambda item: (-item[0], item[1]))
    top_score = scored_matches[0][0]
    top_matches = [name for score, name in scored_matches if score == top_score]

    if len(top_matches) == 1:
        return top_matches[0], None

    return None, build_error_response(
        "manual_name_ambiguous",
        f"Manual name '{requested_manual_name}' is ambiguous.",
        manual_name_requested=requested_manual_name,
        matches=sorted(top_matches),
    )


def normalize_metadata(metadata: dict[str, Any] | None) -> dict[str, str]:
    if not metadata:
        return {}
    return {str(key): str(value) for key, value in metadata.items()}


def get_section_path(metadata: dict[str, str]) -> list[str]:
    return [metadata[key] for key in ("Header 1", "Header 2", "Header 3") if metadata.get(key)]


def split_large_markdown_chunks(chunks) -> list[tuple[str, dict]]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=MAX_CHUNK_CHARS,
        chunk_overlap=CHUNK_OVERLAP_CHARS,
    )

    split_documents = []
    for chunk in chunks:
        content = chunk.page_content.strip()
        if not content:
            continue

        pieces = splitter.split_text(content)
        if not pieces:
            pieces = [content]

        for piece_index, piece in enumerate(pieces):
            metadata = dict(chunk.metadata)
            if len(pieces) > 1:
                metadata["Subchunk"] = str(piece_index + 1)
            split_documents.append((piece, metadata))

    return split_documents


def build_chroma_metadata(
    raw_metadata: dict[str, Any] | None,
    manual_name: str,
    source_file_name: str,
    chunk_index: int,
) -> dict[str, str]:
    metadata = {
        "manual_name": manual_name,
        "source_file": source_file_name,
        "chunk_id": str(chunk_index),
    }

    if raw_metadata:
        for key, value in raw_metadata.items():
            if value is None:
                continue
            key_str = str(key)
            value_str = str(value).strip()
            if value_str:
                metadata[key_str] = value_str

    return metadata


def is_context_length_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "context length" in message or "input length exceeds" in message



def embed_batch_with_retry(batch: list[str], start_index: int) -> list[list[float]]:
    try:
        response = oai_client.embeddings.create(input=batch, model=EMBED_MODEL)
        return [item.embedding for item in response.data]
    except Exception as exc:
        batch_end = start_index + len(batch) - 1
        batch_lengths = [len(doc) for doc in batch]

        if is_context_length_error(exc) and len(batch) > 1:
            midpoint = len(batch) // 2
            left_embeddings = embed_batch_with_retry(batch[:midpoint], start_index)
            right_embeddings = embed_batch_with_retry(batch[midpoint:], start_index + midpoint)
            return left_embeddings + right_embeddings

        if is_context_length_error(exc) and len(batch) == 1:
            raise RuntimeError(
                "Embedding request failed because a single chunk exceeds the embedding model context length. "
                f"Document index: {start_index}. "
                f"Model: '{EMBED_MODEL}'. "
                f"Chunk length: {batch_lengths[0]} characters. "
                "Reduce MAX_CHUNK_CHARS and re-index."
            ) from exc

        raise RuntimeError(
            "Embedding request failed for documents "
            f"{start_index}-{batch_end} using model '{EMBED_MODEL}'. "
            f"Batch size: {len(batch)}. "
            f"Chunk length range: {min(batch_lengths)}-{max(batch_lengths)} characters."
        ) from exc



def embed_documents_in_batches(documents: list[str]) -> list[list[float]]:
    embeddings = []
    for start in range(0, len(documents), EMBED_BATCH_SIZE):
        batch = documents[start:start + EMBED_BATCH_SIZE]
        embeddings.extend(embed_batch_with_retry(batch, start))
    return embeddings


def audit_tool_call(tool_name: str, payload: dict) -> None:
    if not ENABLE_TOOL_AUDIT_LOG:
        return

    TOOL_AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": tool_name,
        **payload,
    }
    with open(TOOL_AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# --- CENTRALIZED INDEXER ---
def index_all_manuals():
    """Scans flat manual folders for exactly one .md file each and indexes them into provider-specific collections."""
    if not DOCS_DIR.exists():
        print(f"❌ Docs directory does not exist: {DOCS_DIR}")
        return
    if not DOCS_DIR.is_dir():
        print(f"❌ Docs directory is not a directory: {DOCS_DIR}")
        return

    excluded_dir_names = get_excluded_dir_names()

    manual_dirs = [
        p for p in DOCS_DIR.iterdir()
        if p.is_dir() and p.name not in excluded_dir_names and not p.name.startswith(".")
    ]

    if not manual_dirs:
        print(f"⚠️ No manual folders found in {DOCS_DIR}. Nothing to index.")
        return

    headers_to_split_on = [("#", "Header 1"), ("##", "Header 2"), ("###", "Header 3")]
    markdown_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=headers_to_split_on)

    indexed_count = 0
    skipped_count = 0
    failed_count = 0
    total_markdown_sections = 0
    total_embedding_chunks = 0

    for manual_dir in manual_dirs:
        try:
            subdirs = [p for p in manual_dir.iterdir() if p.is_dir()]
            if subdirs:
                print(f"❌ Skipping {manual_dir.name}: subfolders are not allowed.")
                skipped_count += 1
                continue

            markdown_files = [p for p in manual_dir.glob("*.md") if p.is_file()]
            if len(markdown_files) != 1:
                print(f"❌ Skipping {manual_dir.name}: expected exactly one .md file, found {len(markdown_files)}.")
                skipped_count += 1
                continue

            md_path = markdown_files[0]
            collection_name = build_collection_name(manual_dir.name)

            print(
                f"\n📖 [{PROVIDER.upper()}] Processing: {md_path.relative_to(DOCS_DIR)} -> Collection: '{collection_name}'"
            )

            with open(md_path, "r", encoding="utf-8") as f:
                md_text = f.read()

            if not md_text.strip():
                print(f"❌ Skipping {manual_dir.name}: markdown file is empty.")
                skipped_count += 1
                continue

            chunks = markdown_splitter.split_text(md_text)
            if not chunks:
                chunks = [type("Chunk", (), {"page_content": md_text, "metadata": {}})()]

            split_documents = split_large_markdown_chunks(chunks)
            print(
                f"✂️ Chunked into {len(chunks)} markdown sections and {len(split_documents)} embedding chunks "
                f"(max {MAX_CHUNK_CHARS} chars). Generating embeddings using {EMBED_MODEL}..."
            )

            documents, ids, metadatas = [], [], []
            for i, (document, metadata) in enumerate(split_documents):
                documents.append(document)
                ids.append(f"chunk_{i}")
                metadatas.append(build_chroma_metadata(metadata, manual_dir.name, md_path.name, i))

            if not documents:
                print(f"❌ Skipping {manual_dir.name}: no indexable content found.")
                skipped_count += 1
                continue

            # Generate vectors using selected client mapping
            embeddings = embed_documents_in_batches(documents)

            try:
                chroma_client.delete_collection(name=collection_name)
            except NotFoundError:
                pass

            collection = chroma_client.get_or_create_collection(name=collection_name)
            collection.upsert(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)

            indexed_count += 1
            total_markdown_sections += len(chunks)
            total_embedding_chunks += len(split_documents)
            print(f"✅ Indexed {md_path.name} successfully!")
        except Exception as exc:
            failed_count += 1
            print(f"❌ Failed to index {manual_dir.name}: {type(exc).__name__}: {exc}")

    print(
        f"\n📊 Indexing summary [{PROVIDER.upper()}]: "
        f"indexed={indexed_count}, "
        f"skipped={skipped_count}, "
        f"failed={failed_count}, "
        f"markdown_sections={total_markdown_sections}, "
        f"embedding_chunks={total_embedding_chunks}"
    )


# --- UNIVERSAL IDE TOOLS ---
@mcp.tool()
def list_reference_manuals() -> dict[str, Any]:
    """
    Lists indexed manuals available for search in the current embedding provider.

    The returned names are valid values for search_reference_manuals(manual_name=...).
    """
    available_manual_names = get_available_manual_names()
    response = {
        "status": "ok",
        "provider": PROVIDER,
        "embed_model": EMBED_MODEL,
        "manual_count": len(available_manual_names),
        "manual_names": available_manual_names,
    }

    audit_tool_call("list_reference_manuals", {
        "provider": PROVIDER,
        "embed_model": EMBED_MODEL,
        "status": "ok",
        "manual_count": len(available_manual_names),
        "available_manual_names": available_manual_names,
    })
    return response


@mcp.tool()
def search_reference_manuals(manual_name: str, query: str, limit: int = 5) -> dict[str, Any]:
    """
    Searches your indexed hardware or software reference manuals.

    manual_name may be an exact indexed manual name or a close human-friendly name
    such as "ngspice" for a manual folder like "ngspice-manual-4p6".
    """
    audit_payload = {
        "manual_name": manual_name,
        "query": query,
        "limit": limit,
        "provider": PROVIDER,
        "embed_model": EMBED_MODEL,
    }
    response_base = {
        "provider": PROVIDER,
        "embed_model": EMBED_MODEL,
        "manual_name_requested": manual_name,
        "query": query,
        "limit": limit,
    }

    if limit < 1:
        response = {
            **response_base,
            **build_error_response(
                "invalid_limit",
                "limit must be at least 1.",
                min_limit=1,
            ),
        }
        audit_tool_call("search_reference_manuals", {
            **audit_payload,
            "status": "error",
            "error": response,
        })
        return response
    if limit > MAX_SEARCH_LIMIT:
        response = {
            **response_base,
            **build_error_response(
                "invalid_limit",
                f"limit must be {MAX_SEARCH_LIMIT} or less.",
                max_search_limit=MAX_SEARCH_LIMIT,
            ),
        }
        audit_tool_call("search_reference_manuals", {
            **audit_payload,
            "status": "error",
            "error": response,
        })
        return response

    available_manual_names = get_available_manual_names()
    resolved_manual_name, resolution_error = resolve_manual_name(manual_name, available_manual_names)
    if resolution_error is not None:
        response = {
            **response_base,
            **resolution_error,
        }
        audit_tool_call("search_reference_manuals", {
            **audit_payload,
            "status": "error",
            "error": response,
            "available_manual_names": available_manual_names,
        })
        return response

    collection_name = build_collection_name(resolved_manual_name)
    audit_payload["resolved_manual_name"] = resolved_manual_name
    audit_payload["collection_name"] = collection_name

    try:
        collection = chroma_client.get_collection(name=collection_name)
    except NotFoundError:
        response = {
            **response_base,
            **build_error_response(
                "collection_not_found",
                (
                    f"Resolved manual '{resolved_manual_name}' was expected at collection "
                    f"'{collection_name}', but the collection was not found. Re-index this provider."
                ),
                manual_name_resolved=resolved_manual_name,
                collection_name=collection_name,
                available_manual_names=available_manual_names,
            ),
        }
        audit_tool_call("search_reference_manuals", {
            **audit_payload,
            "status": "error",
            "error": response,
            "available_manual_names": available_manual_names,
        })
        return response

    try:
        response = oai_client.embeddings.create(input=query, model=EMBED_MODEL)
        query_vector = response.data[0].embedding

        results = collection.query(
            query_embeddings=[query_vector],
            n_results=limit,
            include=["documents", "metadatas", "distances"],
        )

        documents = results.get("documents") or []
        metadatas = results.get("metadatas") or []
        distances = results.get("distances") or []

        documents_for_query = documents[0] if documents else []
        metadatas_for_query = metadatas[0] if metadatas else []
        distances_for_query = distances[0] if distances else []

        structured_results = []
        for index, doc in enumerate(documents_for_query):
            metadata = normalize_metadata(
                metadatas_for_query[index] if index < len(metadatas_for_query) else None
            )
            distance = distances_for_query[index] if index < len(distances_for_query) else None
            structured_results.append({
                "rank": index + 1,
                "section_path": get_section_path(metadata),
                "subchunk": metadata.get("Subchunk"),
                "metadata": metadata,
                "content": doc,
                "distance": float(distance) if distance is not None else None,
            })

        response_payload = {
            **response_base,
            "status": "ok",
            "manual_name_resolved": resolved_manual_name,
            "manual_name_was_resolved": resolved_manual_name != manual_name,
            "collection_name": collection_name,
            "result_count": len(structured_results),
            "results": structured_results,
        }
        audit_tool_call("search_reference_manuals", {
            **audit_payload,
            "status": "ok",
            "result_count": len(structured_results),
            "response_sha256": hashlib.sha256(
                json.dumps(response_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest(),
        })
        return response_payload
    except Exception as exc:
        audit_tool_call("search_reference_manuals", {
            **audit_payload,
            "status": "exception",
            "error_type": type(exc).__name__,
            "error": str(exc),
        })
        raise

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "index":
        index_all_manuals()
    else:
        mcp.run(transport="stdio")