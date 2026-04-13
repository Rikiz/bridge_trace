"""CLI entry point for BridgeTrace."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from bridgetrace.analysis.trace_engine import TraceEngine
from bridgetrace.config import settings
from bridgetrace.core.scanner import Scanner
from bridgetrace.storage.group_manager import GroupManager
from bridgetrace.storage.neo4j_client import Neo4jClient

app = typer.Typer(
    name="bridgetrace",
    help="Cross-repository knowledge mining & call-chain tracing for AI Agents.",
    no_args_is_help=True,
)
group_app = typer.Typer(help="Manage logical groups of repository paths.")
app.add_typer(group_app, name="group")

console = Console()


def _setup_logging(level: str | None = None) -> None:
    lvl = getattr(logging, (level or settings.log_level).upper(), logging.INFO)
    logging.basicConfig(level=lvl, handlers=[RichHandler(console=console)], format="%(message)s")


@app.command()
def scan(
    group: str = typer.Argument(..., help="Group name to scan."),
    bootstrap: bool = typer.Option(
        False, "--bootstrap", help="Bootstrap Neo4j schema before scan."
    ),
    clean: bool = typer.Option(
        False, "--clean", help="Delete existing graph data for this group before scanning."
    ),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON."),
) -> None:
    """Execute a full scan on all paths in the given group."""
    _setup_logging()

    with GroupManager() as gm:
        grp = gm.get(group)
        if grp is None:
            console.print(
                f"[red]Group '{group}' not found. Create it with: bridgetrace group add[/red]"
            )
            raise typer.Exit(1)

    paths = [Path(p) for p in grp.paths]
    scanner = Scanner()
    results = scanner.scan_paths(paths)

    nodes, edges = scanner.build_graph_entities(results, group, grp.paths)

    with Neo4jClient() as client:
        if bootstrap:
            client.bootstrap_schema()

        if clean:
            deleted = client.clean_group(group)
            console.print(f"[yellow]Cleaned {deleted} existing nodes for group '{group}'[/yellow]")

        client.batch_merge_nodes(nodes)
        client.batch_merge_edges(edges)

    if json_output:
        scan_data = {
            "group": group,
            "files_scanned": len(results),
            "nodes_created": len(nodes),
            "edges_created": len(edges),
            "uris_found": sum(len(r.uris) for r in results),
            "functions_found": sum(len(r.functions) for r in results),
            "calls_found": sum(len(r.calls) for r in results),
        }
        console.print_json(json.dumps(scan_data))
    else:
        console.print(
            f"[green]Scan complete[/green]: {len(results)} files, "
            f"{len(nodes)} nodes, {len(edges)} edges"
        )


@app.command()
def trace(
    uri: str = typer.Argument(..., help="URI path to trace."),
    group: str | None = typer.Option(
        None, "--group", "-g", help="Limit trace to a specific group."
    ),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON."),
    impl: bool = typer.Option(False, "--impl", help="Trace to backend implementation."),
    cross_repo: bool = typer.Option(
        False, "--cross-repo", help="Trace cross-repo routing (gateway → backend)."
    ),
) -> None:
    """Trace the full topology chain for a given URI."""
    _setup_logging()

    with Neo4jClient() as client:
        engine = TraceEngine(client)
        if impl and group:
            result = engine.trace_uri_to_implementation(uri, group)
        elif cross_repo:
            result = engine.trace_cross_repo(uri)
        else:
            result = engine.trace_uri(uri, group)

    if json_output:
        console.print_json(json.dumps(result.to_dict_list()))
    else:
        console.print(result.format_text())


@app.command("trace-endpoint-calls")
def trace_endpoint_calls(
    uri: str = typer.Argument(..., help="URI path of the source endpoint."),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON."),
) -> None:
    """Trace which other endpoints are called by the given endpoint."""
    _setup_logging()

    with Neo4jClient() as client:
        engine = TraceEngine(client)
        result = engine.trace_endpoint_calls(uri)

    if json_output:
        console.print_json(json.dumps(result.to_dict_list()))
    else:
        console.print(result.format_text())


@app.command("trace-consumers")
def trace_consumers(
    uri: str = typer.Argument(..., help="URI path of the endpoint."),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON."),
) -> None:
    """Find which functions consume the given endpoint via HTTP calls."""
    _setup_logging()

    with Neo4jClient() as client:
        engine = TraceEngine(client)
        result = engine.trace_consumers(uri)

    if json_output:
        console.print_json(json.dumps(result.to_dict_list()))
    else:
        console.print(result.format_text())


@group_app.command("add")
def group_add(
    name: str = typer.Argument(..., help="Group name."),
    paths: list[str] = typer.Argument(..., help="Repository paths to bind."),  # noqa: B008
) -> None:
    """Add or update a logical group with the given paths."""
    _setup_logging()
    with GroupManager() as gm:
        grp = gm.add(name, paths)
    console.print(f"[green]Group '{name}' saved[/green] with {len(grp.paths)} paths")


@group_app.command("list")
def group_list() -> None:
    """List all groups."""
    _setup_logging()
    with GroupManager() as gm:
        groups = gm.list_groups()

    if not groups:
        console.print("[yellow]No groups found.[/yellow]")
        return

    table = Table(title="BridgeTrace Groups")
    table.add_column("Name", style="cyan")
    table.add_column("Paths", style="green")
    table.add_column("Updated", style="dim")

    for g in groups:
        table.add_row(g.name, "\n".join(g.paths), g.updated_at.isoformat())
    console.print(table)


@group_app.command("remove")
def group_remove(
    name: str = typer.Argument(..., help="Group name to remove."),
) -> None:
    """Remove a group by name."""
    _setup_logging()
    with GroupManager() as gm:
        removed = gm.remove(name)
    if removed:
        console.print(f"[green]Group '{name}' removed.[/green]")
    else:
        console.print(f"[red]Group '{name}' not found.[/red]")


@app.command()
def bootstrap() -> None:
    """Bootstrap the Neo4j schema (constraints & indexes)."""
    _setup_logging()
    with Neo4jClient() as client:
        client.bootstrap_schema()
    console.print("[green]Neo4j schema bootstrapped.[/green]")


if __name__ == "__main__":
    app()
