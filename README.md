# BridgeTrace

Cross-repository knowledge mining & call-chain tracing tool for AI Agents.

## Overview

BridgeTrace scans heterogeneous codebases (Java, Python, TypeScript), extracts URI paths from configuration files and source code, builds a knowledge graph in Neo4j, and enables end-to-end call-chain tracing across repository boundaries.

### Why BridgeTrace?

- **Value-Centric Detection**: Ignores keys, extracts URI patterns from any JSON/YAML value.
- **Multi-Language Semantic Parsing**: Tree-sitter powered extraction for Python, TypeScript, and Java.
- **Artifact Inspection**: Reads compiled `.class` files via `javap -v` to extract annotation routes.
- **Gitignore-Agnostic**: `IGNORE_GITIGNORE=True` by default — scans everything, including generated code.
- **Neo4j Knowledge Graph**: Full schema with `Group → Repo → File → Function/Endpoint` topology.
- **AI Agent Ready**: `--json` flag outputs structured data for programmatic consumption.

## Directory Structure

```
bridgetrace/
├── pyproject.toml
├── .env.example
├── README.md
└── src/
    └── bridgetrace/
        ├── __init__.py
        ├── config.py                  # pydantic-settings configuration
        ├── cli/
        │   ├── __init__.py
        │   └── app.py                # Typer CLI (scan, trace, group)
        ├── core/
        │   ├── __init__.py
        │   └── scanner.py            # File discovery & graph entity builder
        ├── models/
        │   ├── __init__.py
        │   ├── graph.py              # Graph node/edge & parse result models
        │   └── group.py              # Group management model
        ├── parsers/
        │   ├── __init__.py
        │   ├── base.py                # Abstract parser base class
        │   ├── json_parser.py         # Value-centric JSON/YAML URI detector
        │   ├── treesitter_parser.py   # Tree-sitter semantic extractor
        │   └── artifact_parser.py     # Java .class annotation inspector
        ├── storage/
        │   ├── __init__.py
        │   ├── neo4j_client.py        # Neo4j driver + batch UNWIND writer
        │   └── group_manager.py       # SQLite-backed group persistence
        └── analysis/
            ├── __init__.py
            └── trace_engine.py       # Cross-repo Cypher trace queries
```

## Setup

### Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) package manager
- Neo4j 5.x (running instance)
- JDK (for `javap` artifact inspection)

### Install

```bash
cp .env.example .env          # edit with your Neo4j credentials
uv sync                       # install dependencies
```

### Configuration

All settings are loaded from `.env` (or environment variables):

| Variable | Default | Description |
|---|---|---|
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j connection URI |
| `NEO4J_USER` | `neo4j` | Neo4j username |
| `NEO4J_PASSWORD` | — | Neo4j password |
| `NEO4J_DATABASE` | `neo4j` | Neo4j database name |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `IGNORE_GITIGNORE` | `True` | Scan files even if .gitignore excludes them |
| `SQLITE_PATH` | `~/.bridgetrace/groups.db` | Local group metadata DB |

## Usage

### Bootstrap Neo4j Schema

```bash
bridgetrace bootstrap
```

### Manage Groups

```bash
# Add a group binding multiple repo paths
bridgetrace group add myservice /repos/gateway /repos/auth-service /repos/user-api

# List all groups
bridgetrace group list

# Remove a group
bridgetrace group remove myservice
```

### Scan

```bash
# Full scan (respects IGNORE_GITIGNORE setting)
bridgetrace scan myservice

# Scan with schema bootstrap
bridgetrace scan myservice --bootstrap

# JSON output for AI Agent consumption
bridgetrace scan myservice --json
```

### Trace

```bash
# Trace a URI across the entire graph
bridgetrace trace "/api/v1/users/{id}"

# Trace within a specific group
bridgetrace trace "/api/v1/users/{id}" --group myservice

# Trace to backend implementation
bridgetrace trace "/api/v1/users/{id}" --group myservice --impl

# Structured JSON output
bridgetrace trace "/api/v1/users/{id}" --json
```

## Neo4j Graph Schema

### Nodes

| Label | Key Fields | Description |
|---|---|---|
| `Group` | `id`, `name` | Logical grouping of repositories |
| `Repo` | `id`, `name` | A source code repository |
| `File` | `id`, `path` | A scanned source file |
| `Function` | `id`, `name`, `line`, `snippet` | A function/method definition |
| `Endpoint` | `id`, `uri` | A URI path extracted from code/config |

### Edges

| Relationship | From → To | Meaning |
|---|---|---|
| `CONTAINS` | Group → Repo, Repo → File, File → Function/Endpoint | Ownership hierarchy |
| `IMPLEMENTS` | Function → Function | Backend route implementation |
| `CALLS_INTERNAL` | Function → Function | Same-repo function call |
| `CALLS_EXTERNAL` | Function → Function | Cross-repo function call |

## Parser Details

### JSON/YAML Value-Centric Detector

Recursively walks all values in JSON/YAML documents. Matches strings against:

```
^/(?:[\w\-\.]+/)+[\w\-\.]*$
```

Does **not** inspect keys — purely value-driven extraction.

### Tree-Sitter Semantic Extractor

For `.py`, `.ts`, `.tsx`, `.java`:

- **String literals** matching the URI pattern
- **Function definitions** with name, line number, and code snippet
- **Internal call graph** (caller → callee relationships)

### Artifact Inspector

For `.class` files, runs `javap -v` and extracts annotation path strings.

## Development

```bash
uv sync --dev
uv run pytest
uv run ruff check .
uv run mypy src/
```

## License

MIT