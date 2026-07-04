# Docs MCP Server

Tools like SPICE, Verilog, and TeX expose a weakness of LLM-based coding assistants and agents: these tools are common enough that the model may know "something" about them, but it often guesses incorrectly. For example, if you ask about the `.TRAN` syntax in SPICE, you may get something that looks plausible but is wrong, such as `.TRAN Tstart Tstop Tstep`.

This server is meant to solve that problem. It indexes **your** local Markdown manuals into Chroma and serves them as a **stdio MCP server**. MCP clients such as coding agents in an IDE or plugin should usually start the server process themselves rather than requiring you to run a separate long-lived service manually. The server provides `list_reference_manuals` and `search_reference_manuals` tools that return structured objects instead of human-formatted Markdown.

For example, a tool-assisted answer might look like this:

* `.TRAN` syntax in SPICE
  * I’ll search for information about `.TRAN` syntax in SPICE simulation.
  * Using the local `search_reference_manuals` tool...
    * Based on the NGSPICE manual, here’s the `.TRAN` (transient analysis) syntax and explanation:
```SPICE
.TRAN Tstep Tstop [ Tstart [ Tmax ] ] [ UIC ]
```

Instructions to enable the Continue coding agent to automatically start the MCP server are given below, but the setup should be similar for other MCP-capable tools.

## Requirements

To use this server, you need:

1. **Python 3** so you can create the virtual environment and run `server.py`.
2. **An embedding model** for indexing and search.
3. **A coding assistant or AI IDE that can use MCP** so it can call this server's tools.

### Embedding model

An embedding model converts text into numeric vectors that capture semantic meaning. In simple terms, it turns a chunk of text such as a manual section, and a user query such as "find the syntax for `.TRAN`", into comparable vector representations.

This server uses embeddings in two places:

- **Indexing:** it reads your markdown manuals, splits them into chunks, generates embeddings for those chunks, and stores them in Chroma.
- **Lookup/search:** it embeds the user's query, compares that query embedding to the stored manual chunk embeddings, and returns the closest matches.

You can get an embedding model in either of these ways:

- **OpenAI API**
  - set `EMBEDDING_PROVIDER="openai"`
  - provide an `OPENAI_API_KEY`
  - the server uses OpenAI's `text-embedding-3-small` model
- **Ollama (local)**
  - install and run Ollama on your machine
  - use `EMBEDDING_PROVIDER="ollama"`
  - optionally set `OLLAMA_EMBED_MODEL`
  - by default, the server uses `nomic-embed-text`

If you use OpenAI, you are getting embeddings through a hosted API key. If you use Ollama, you are getting embeddings from a local model running on your machine.

### MCP-capable coding assistant or AI IDE

You also need a client application that supports MCP and can call this server's tools.

In this README:

- the **server** is this project: `server.py`, which exposes tools such as `list_reference_manuals` and `search_reference_manuals`
- the **client** is the application that launches and talks to this server, such as Continue, Claude Desktop, Claude Code, or an MCP-capable VS Code setup

What matters is not the assistant name, but whether the **client application** can:

- launch `server.py` as a **stdio MCP server**
- expose its tools to the assistant
- allow tool calls during chat or coding sessions

This README uses **Continue** in VS Code or VSCodium as the example client, but other MCP-capable setups may also work, including:
- **Claude Code**
- **Claude Desktop**
- **VS Code with GitHub Copilot**, when MCP support is available and configured
- other assistants or IDE integrations that support **stdio MCP servers**

Support varies by product version and configuration, so check your client's current MCP documentation if needed.

## Cloning the repo 

Clone the repository from GitHub with:

```bash README.md
git clone https://github.com/ejfogleman/docs_mcp.git
cd docs_mcp
```

If you want to place it somewhere specific on your machine, clone into that directory instead:

```bash README.md
git clone https://github.com/ejfogleman/docs_mcp.git /path/to/your/DOCS_DIR
cd /path/to/your/DOCS_DIR
```

## Setting up the virtual environment

The repo includes a `requirements.txt` file.  Create the local virtual environment and install the dependencies with:

```bash README.md
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## What `DOCS_DIR` means

`DOCS_DIR` means the root folder of this repository on **your** machine.

When you clone this repo, the directory you clone it into becomes your `DOCS_DIR`.

For example, if you clone the repo into:

- `/home/ejf/Projects/docs_mcp`
- `~/Projects/docs_mcp`

then that path is your `DOCS_DIR`.

In the examples below, any path that looks like `~/Projects/docs` is just an example. Replace it with the actual location where **you** cloned this repo.

Inside the server, `DOCS_DIR` is also an optional environment variable you can set to override where manuals are loaded from. But for most users reading this README, the main idea is simple: `DOCS_DIR` is the top-level folder of this cloned repo.

## Directory layout

The MCP Server utilizes manuals in markdown format.  They must be indexed first before the MCP server can provide them to the coding agent. 

- Each manual lives in its own top-level folder.
- Each manual folder must be flat.
- Each manual folder must contain exactly one `.md` file.
- The folder name is the manual name used by `search_reference_manuals`.

Example:

```text README.md
DOCS_DIR/
├── ngspice-manual-4p6/
│   └── ngspice-manual-4p6.md
├── another-manual/
│   └── another-manual.md
├── server.py
└── README.md
```

## Indexing

The index is provider-specific. If you index with OpenAI, those collections are separate from Ollama collections. OpenAI and Ollama indexes can coexist.

### OpenAI

```bash README.md
cd /path/to/your/DOCS_DIR
source .venv/bin/activate
EMBEDDING_PROVIDER="openai" OPENAI_API_KEY="your-key-here" python3 server.py index
```

### Ollama

```bash README.md
cd /path/to/your/DOCS_DIR
source .venv/bin/activate
EMBEDDING_PROVIDER="ollama" python3 server.py index
```

### Which embedding model gets used?

For **OpenAI**, the server always uses `text-embedding-3-small`. There is currently no `OPENAI_EMBED_MODEL` setting.

This is intentional:

- it keeps the OpenAI setup simple
- it gives predictable indexing and search behavior across machines
- it avoids accidental mismatches where documents are indexed with one OpenAI embedding model and queried with another

For **Ollama**, the embedding model is configurable through `OLLAMA_EMBED_MODEL`, and defaults to `nomic-embed-text`.

If you change providers or change the Ollama embedding model, re-index before searching.

## Running the MCP server

There are two ways to run the MCP server:

1. Run it manually in a terminal.
2. Let your MCP client start it for you.

If you are using a tool like Continue, you usually do **not** start the server yourself. Instead, Continue launches `server.py` automatically using the `command`, `args`, and `env` values from your `config.yaml`.

Manual startup is mainly useful for debugging or for testing the server outside an MCP client.

### Manual startup

```bash README.md
cd /path/to/your/DOCS_DIR
source .venv/bin/activate
python3 server.py
```

When started this way, the server runs over stdio and waits for an MCP client to connect.

### Client-managed startup

If you use Continue or another MCP-capable client, configure that client to launch this script over stdio. See the `Continue config (config.yaml)` section below for an example. In that setup, the client starts the server process automatically when needed, so you should not also start a second copy manually.

## MCP tools

### `list_reference_manuals()`

Returns a structured object with:

- `status`
- `provider`
- `embed_model`
- `manual_count`
- `manual_names`

### `search_reference_manuals(manual_name, query, limit=5)`

Searches an indexed manual and returns a structured object.

Notes:

- `manual_name` may be exact or fuzzy.
- Example exact manual name: `ngspice-manual-4p6`
- Example fuzzy manual name: `ngspice`
- `limit` must be at least `1` and no greater than `MAX_SEARCH_LIMIT`

Successful responses include:

- `status`
- `provider`
- `embed_model`
- `manual_name_requested`
- `manual_name_resolved`
- `manual_name_was_resolved`
- `collection_name`
- `query`
- `limit`
- `result_count`
- `results`

Each item in `results` includes:

- `rank`
- `section_path`
- `subchunk`
- `metadata`
- `content`
- `distance`

Error responses are also structured and include fields such as:

- `status: "error"`
- `error_code`
- `message`

Possible `error_code` values include:

- `invalid_limit`
- `manual_not_found`
- `manual_name_ambiguous`
- `collection_not_found`

## Continue config (`config.yaml`)

Use your own absolute `DOCS_DIR` path in this config. Do not copy `/home/ejf/Projects/docs` unless that is actually where you cloned the repo.

### OpenAI

```yaml README.md
mcpServers:
  - name: "docs_mcp"
    command: "/absolute/path/to/your/DOCS_DIR/.venv/bin/python3"
    args:
      - "/absolute/path/to/your/DOCS_DIR/server.py"
    env:
      EMBEDDING_PROVIDER: "openai"
      OPENAI_API_KEY: "your-actual-api-key-here"
```

### Ollama

```yaml README.md
mcpServers:
  - name: "docs_mcp"
    command: "/absolute/path/to/your/DOCS_DIR/.venv/bin/python3"
    args:
      - "/absolute/path/to/your/DOCS_DIR/server.py"
    env:
      EMBEDDING_PROVIDER: "ollama"
      OLLAMA_EMBED_MODEL: "nomic-embed-text"
```

### Enabling the audit log in Continue

Audit logging is disabled by default. To turn it on temporarily for debugging, add `ENABLE_TOOL_AUDIT_LOG: "1"` under the server `env` block in your Continue `config.yaml`.

Example:

```yaml README.md
mcpServers:
  - name: "docs_mcp"
    command: "/absolute/path/to/your/DOCS_DIR/.venv/bin/python3"
    args:
      - "/absolute/path/to/your/DOCS_DIR/server.py"
    env:
      EMBEDDING_PROVIDER: "openai"
      OPENAI_API_KEY: "your-actual-api-key-here"
      ENABLE_TOOL_AUDIT_LOG: "1"
      TOOL_AUDIT_LOG_PATH: "/absolute/path/to/your/DOCS_DIR/mcp_tool_audit.jsonl"
```

If you omit `TOOL_AUDIT_LOG_PATH`, the default audit log file is `mcp_tool_audit.jsonl` in your `DOCS_DIR`. Leave `ENABLE_TOOL_AUDIT_LOG` unset during normal use if you do not want log files to accumulate.

## Testing prompts

You do **not** need to talk to the model in formal MCP syntax.

In normal use, just ask naturally. If your MCP client is configured correctly, the model should decide when to call `list_reference_manuals` or `search_reference_manuals` on its own.  You will see a message like `Continue used the Local manuals list_reference_manuals tool` in the agent output.

For example, prompts like these should be enough:

```text README.md
What reference manuals are available?
```

```text README.md
Search the ngspice manual for xspice.
```

```text README.md
In the ngspice manual, find the syntax for measuring period.
```

If you want to explicitly test whether tool calling works, you can still use more direct prompts such as:

```text README.md
Please call list_reference_manuals.
```

```text README.md
Please search the ngspice manual for "measure period syntax".
```

The important point for newcomers is that the MCP wiring is handled by the client configuration. Once that is set up, your prompts can usually be plain English.

## Optional environment variables

| Variable | What it does | Default |
|---|---|---|
| `OLLAMA_EMBED_MODEL` | Embedding model name used when `EMBEDDING_PROVIDER="ollama"`. OpenAI does not currently expose a corresponding model override; it always uses `text-embedding-3-small`. | `nomic-embed-text` |
| `EMBED_BATCH_SIZE` | Number of document chunks sent per embedding request batch. | `100` |
| `MAX_SEARCH_LIMIT` | Maximum allowed `limit` value for `search_reference_manuals`. | `20` |
| `MAX_CHUNK_CHARS` | Maximum chunk size, in characters, after markdown sections are split further for embedding. | `4000` |
| `CHUNK_OVERLAP_CHARS` | Overlap, in characters, between adjacent embedding chunks. | `400` |
| `DOCS_DIR` | Root directory containing manual folders. | The directory containing `server.py` (normally your cloned repo root). |
| `DB_PATH` | Path to the Chroma persistent database. | `.central_vector_db` inside `DOCS_DIR`/the directory containing `server.py` |
| `ENABLE_TOOL_AUDIT_LOG` | Enables JSONL audit logging for MCP tool calls. Accepted truthy values are `1`, `true`, `yes`, and `on`. | Disabled |
| `TOOL_AUDIT_LOG_PATH` | Output path for the JSONL audit log file. Only used when audit logging is enabled. | `mcp_tool_audit.jsonl` in `DOCS_DIR`/the directory containing `server.py` |

If you want Continue to enable audit logging when it launches the MCP server, add `ENABLE_TOOL_AUDIT_LOG: "1"` under the server `env` block in your `config.yaml`. Leave it unset during normal use if you do not want log files to accumulate.

## Converting manuals to markdown

This server indexes markdown manuals, so PDF manuals usually need to be converted first. Two useful command-line tools for this are `marker_single` and `markitdown`.

After conversion, make sure the result matches this repository's layout rules:

- create one top-level folder per manual
- keep that folder flat
- place exactly one `.md` file in that folder
- use the folder name as the manual name you want to search

### Example using `marker_single`

If you have a PDF such as `my-manual.pdf`, one workflow is:

```bash README.md
mkdir -p my-manual
marker_single my-manual.pdf --output_dir my-manual
```

Depending on your local `marker_single` version and options, it may produce one or more output files inside `my-manual/`. Keep the markdown file you want, rename it if needed, and make sure the final directory contains exactly one `.md` file.

For example, the finished layout should look like:

```text README.md
DOCS_DIR/
├── my-manual/
│   └── my-manual.md
├── server.py
└── README.md
```

### Example using `markitdown`

A simple `markitdown` workflow is:

```bash README.md
mkdir -p my-manual
markitdown my-manual.pdf > my-manual/my-manual.md
```

This writes the converted markdown directly into the manual folder in the format this server expects.

### After conversion

Once the markdown file is in the correct folder layout, index it as usual:

```bash README.md
cd /path/to/your/DOCS_DIR
source .venv/bin/activate
python3 server.py index
```

## License

Copyright 2026 Eric Fogleman

This project is licensed under the Apache License 2.0. See the `LICENSE` file for the full text.
The main server source file, `server.py`, also includes an Apache-2.0 license header.

