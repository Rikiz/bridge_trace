# BridgeTrace

Cross-repository knowledge mining & call-chain tracing tool for AI Agents.

## Overview

BridgeTrace scans heterogeneous codebases (Java, Python, TypeScript), extracts URI paths from source code and configuration files, builds a Neo4j knowledge graph, and enables end-to-end call-chain tracing across repository boundaries.

It specifically handles **gateway-to-backend URI prefix mismatch** — where a gateway config declares `/data/v1/tenant-configs/${id}` but the backend controller implements `/v1/tenant-configs/{id}` — via URI suffix fuzzy matching with path-parameter normalization.

### Why BridgeTrace?

- **Value-Centric Detection**: Ignores keys, extracts URI patterns from any JSON/YAML value.
- **Multi-Language Semantic Parsing**: Tree-sitter powered extraction for Python, TypeScript, and Java — including annotations, decorators, and HTTP client calls.
- **Artifact Inspection**: Reads compiled `.class` files via `javap -v` to extract annotation routes.
- **HTTP Call Detection**: Identifies `restTemplate.getForObject`, `requests.get`, `axios.get`, `fetch`, and similar patterns — linking calling functions to the endpoints they consume.
- **Cross-Repo Routing**: Gateway declarations are linked to backend implementations through URI suffix matching with path-param normalization (`${id}` ≡ `{id}` ≡ `{}`).
- **Multi-Level .gitignore**: Respects nested `.gitignore` files throughout the directory tree.
- **Neo4j Knowledge Graph**: Full schema with `Group → Repo → File → Function/Endpoint` topology plus cross-cutting edges.
- **AI Agent Ready**: `--json` flag outputs structured data for programmatic consumption.

## Directory Structure

```
bridgetrace/
├── pyproject.toml
├── .env.example
├── README.md
└── src/bridgetrace/
    ├── __init__.py
    ├── config.py                  # pydantic-settings configuration
    ├── utils.py                   # Cross-platform path normalization
    ├── cli/
    │   ├── __init__.py
    │   └── app.py                # Typer CLI (scan, trace, trace-endpoint-calls, trace-consumers, group, bootstrap)
    ├── core/
    │   ├── __init__.py
    │   └── scanner.py            # 6-phase graph builder
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
    │   ├── neo4j_client.py        # Neo4j driver + batch UNWIND writer + clean_group
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
| `SCAN_BATCH_SIZE` | `500` | UNWIND batch size for Neo4j writes |

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

# Clean existing data before scanning (prevents duplicates)
bridgetrace scan myservice --clean

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

# Trace cross-repo routing (gateway → backend)
bridgetrace trace "/data/v1/users/{id}" --cross-repo

# Structured JSON output
bridgetrace trace "/api/v1/users/{id}" --json
```

### Trace Endpoint Calls

```bash
# Which other endpoints does this endpoint call?
bridgetrace trace-endpoint-calls "/api/v1/users/{id}"
```

### Trace Consumers

```bash
# Which functions consume this endpoint via HTTP?
bridgetrace trace-consumers "/v1/users/{id}"
```

## Neo4j Graph Schema

### Nodes

| Label | Key Fields | Description |
|---|---|---|
| `Group` | `id`, `name` | Logical grouping of repositories |
| `Repo` | `id`, `name` | A source code repository |
| `File` | `id`, `path` | A scanned source file |
| `Function` | `id`, `name`, `line`, `snippet`, `file_path` | A function/method definition |
| `Endpoint` | `id`, `uri`, `role`, `file_path`, `function_name` | A URI path extracted from code/config |

### Endpoint Roles

| Role | Meaning |
|---|---|
| `declaration` | URI extracted from JSON/YAML config files (gateway routes, etc.) |
| `implementation` | URI extracted from annotations/decorators (`@GetMapping`, `@app.route`, etc.) or `.class` files |
| `reference` | URI found in source code string literals or HTTP client calls |

### Edges

| Relationship | From → To | Meaning |
|---|---|---|
| `CONTAINS` | Group → Repo, Repo → File, File → Function/Endpoint | Ownership hierarchy |
| `CALLS_INTERNAL` | Function → Function | Same-repo function call |
| `CALLS_EXTERNAL` | Function → Function | Cross-repo function call |
| `CONSUMES` | Function → Endpoint | Function makes an HTTP call to this endpoint |
| `IMPLEMENTS` | Function → Endpoint | Function implements this endpoint (via annotation/decorator) |
| `IMPLEMENTED_BY` | Endpoint → Function | Inverse of IMPLEMENTS |
| `DEFINED_IN` | Endpoint → File | Endpoint is defined in this file |
| `ROUTES_TO` | Endpoint → Endpoint | Declaration endpoint routes to implementation endpoint (suffix match) |
| `CALLS` | Endpoint → Endpoint | Derived: endpoint A calls endpoint B (via CONSUMES, ROUTES_TO, or function call chain) |

## Scanner: 6-Phase Graph Build

The scanner converts parse results into Neo4j-ready nodes and edges in six phases:

1. **Phase 1**: Create all nodes (Group, Repo, File, Endpoint, Function) + `CONTAINS` edges
2. **Phase 2**: `CALLS_INTERNAL` / `CALLS_EXTERNAL` edges between Functions
3. **Phase 2.5**: `CONSUMES` edges (Function → Endpoint via HTTP calls)
4. **Phase 3**: `IMPLEMENTS`, `IMPLEMENTED_BY`, `DEFINED_IN` edges
5. **Phase 3.5**: `ROUTES_TO` edges (declaration → implementation via URI suffix matching)
6. **Phase 4**: Endpoint `CALLS` Endpoint (derived from CONSUMES + IMPLEMENTS, ROUTES_TO, and function call chains)

## Parser Details

### JSON/YAML Value-Centric Detector

Recursively walks all values in JSON/YAML documents. Matches strings against:

```
^/(?:[\w\-\.]+|\$\{[\w\-\.]+\}|\{[\w\-\.]+\})(?:/(?:[\w\-\.]+|\$\{[\w\-\.]+\}|\{[\w\-\.]+\}))*$
```

Supports path parameters in both `${id}` (gateway config) and `{id}` (backend annotations) forms. Does **not** inspect keys — purely value-driven extraction.

### Tree-Sitter Semantic Extractor

For `.py`, `.ts`, `.tsx`, `.java`:

- **String literals** matching the URI pattern
- **Function definitions** with name, line number, and code snippet
- **Internal/external call graph** (caller → callee relationships)
- **Endpoint implementations** from annotations (`@RequestMapping`, `@GetMapping`, `@app.route`, `@Post`) and decorators
- **HTTP client calls** detecting `restTemplate.*`, `requests.*`, `axios.*`, `fetch` patterns
- **Java support** for `method_declaration`, `constructor_declaration`, and `lambda_expression` as enclosing function scopes

### Artifact Inspector

For `.class` files, runs `javap -v` and extracts annotation path strings. Validates extracted URIs against the same `URI_PATH_RE` used by the JSON parser, including full path-parameter support.

### URI Suffix Matching

Cross-repo routing is established by comparing URI tail segments after normalizing path parameters:

- `/data/v1/tenant-configs/${id}` → normalized: `/data/v1/tenant-configs/{}`
- `/v1/tenant-configs/{id}` → normalized: `/v1/tenant-configs/{}`
- Tail match: `v1/tenant-configs/{}` === `v1/tenant-configs/{}` → `ROUTES_TO` edge created

## Development

```bash
uv sync --dev
uv run pytest -o addopts=""
uv run ruff check .
uv run mypy src/
```

## License

MIT
