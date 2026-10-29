"""Unified CLI: ingest, search, audit. Run as:
    python -m compliance_rag.cli ingest ./documents
"""
from __future__ import annotations

from pathlib import Path

import typer

from compliance_rag import config
from compliance_rag.audit.runner import run_audit
from compliance_rag.audit.rules import load_rules
from compliance_rag.ingestion.pipeline import SUPPORTED, ingest_file
from compliance_rag.retrieval.search import fetch_chunks, hybrid_search
from compliance_rag.storage.db import get_conn

app = typer.Typer(no_args_is_help=True)
audit_app = typer.Typer(no_args_is_help=True)
app.add_typer(audit_app, name="audit")


@app.command()
def ingest(folder: str) -> None:
    """Parse, chunk, and index every supported document in `folder`."""
    paths = sorted(p for p in Path(folder).rglob("*") if p.suffix.lower() in SUPPORTED)
    if not paths:
        typer.secho(f"No supported files under {folder}", fg=typer.colors.RED)
        raise typer.Exit(1)

    total = 0
    for path in paths:
        result = ingest_file(path)
        if result.status == "unreadable":
            typer.secho(f"skipped (unreadable): {path.name}", fg=typer.colors.YELLOW)
            continue
        if result.status == "already_indexed":
            typer.secho(f"already indexed: {path.name} doc_id={result.doc.doc_id}",
                        fg=typer.colors.YELLOW)
            continue
        total += result.chunks
        typer.secho(f"indexed {path.name} → {result.chunks} chunks ({result.doc.domain}) "
                    f"doc_id={result.doc.doc_id}", fg=typer.colors.GREEN)
    typer.secho(f"Done. {total} chunks searchable.", fg=typer.colors.BRIGHT_GREEN)


@app.command()
def docs() -> None:
    """List ingested documents and their doc_ids."""
    with get_conn() as conn:
        rows = conn.execute("SELECT doc_id, filename, domain, ingested_at FROM documents "
                            "ORDER BY ingested_at DESC").fetchall()
    for doc_id, filename, domain, ingested_at in rows:
        typer.echo(f"{doc_id}  {filename}  ({domain}, {ingested_at:%Y-%m-%d %H:%M})")


@app.command()
def search(query: str, top: int = 5, doc: str | None = None) -> None:
    """Hybrid search: dense + BM25, fused with RRF. --doc limits it to one document."""
    chunk_ids = hybrid_search(query, top, doc)
    if not chunk_ids:
        typer.secho("No matches.", fg=typer.colors.YELLOW)
        return
    conn = get_conn()
    for c in fetch_chunks(chunk_ids):
        typer.echo("")
        typer.secho(f"[{c['doc_id']} · p.{c['page']} · {c['section']} · {c['chunk_type']}]",
                    fg=typer.colors.CYAN)
        typer.echo(c["text"][:400] + ("…" if len(c["text"]) > 400 else ""))


@audit_app.command()
def run(pack: str, domain: str | None = None, doc: str | None = None) -> None:
    """Run every rule in a pack against one document (--doc) or the whole corpus."""
    def progress(rule, event, detail):
        color = (typer.colors.GREEN if event == "compliant"
                 else typer.colors.RED if event == "non_compliant"
                 else typer.colors.YELLOW)
        typer.secho(f"{rule.rule_id}: {event.upper()}"
                    + (f" ({detail})" if detail else ""), fg=color)

    audit_id, summary = run_audit(Path(pack), domain, progress, doc)
    stopped = summary.pop("stopped", None)
    typer.secho(f"Audit {audit_id} {'stopped early' if stopped else 'complete'}:",
                fg=typer.colors.RED if stopped else typer.colors.BRIGHT_GREEN)
    for k, v in summary.items():
        typer.echo(f"  {k:18} {v}")
    if stopped:
        typer.secho(f"  {stopped} Findings so far are saved.", fg=typer.colors.RED)
    typer.echo(f"  View:  python -m compliance_rag.cli audit findings --audit {audit_id}")


@audit_app.command()
def rules(pack: str) -> None:
    """List the rules in a pack."""
    pack_id, version, rls = load_rules(Path(pack))
    typer.echo(f"{pack_id} v{version} — {len(rls)} rules")
    for r in rls:
        typer.secho(f"  {r.rule_id} [{r.severity}] {r.description}", fg=typer.colors.CYAN)


@audit_app.command()
def findings(audit: str) -> None:
    """List all findings for an audit with their citations."""
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT f.finding_id, f.rule_id, f.status, f.severity, f.text, f.confidence, f.validated "
            "FROM findings f WHERE f.audit_id = %s ORDER BY f.severity, f.rule_id", (audit,),
        )
        rows = cur.fetchall()
        for fid, rule_id, status, severity, text, conf, validated in rows:
            color = (typer.colors.GREEN if status == "compliant"
                     else typer.colors.RED if status == "non_compliant"
                     else typer.colors.YELLOW)
            typer.echo("")
            typer.secho(f"[{severity.upper()}] {rule_id} → {status} "
                        f"(confidence {conf:.2f}, validated={validated})", fg=color)
            typer.echo(f"  {text}")
            cur.execute(
                "SELECT doc_id, page, section, quote, match_score "
                "FROM citations WHERE finding_id = %s", (fid,),
            )
            for doc_id, page, section, quote, score in cur.fetchall():
                typer.secho(f"  ↳ {doc_id} p.{page} §{section} (match {score:.2f}):",
                            fg=typer.colors.CYAN)
                typer.echo(f"     “{quote}”")


if __name__ == "__main__":
    app()
