"""Streamlit compliance audit dashboard. Run: streamlit run dashboard/app.py"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pandas as pd
import psycopg
import streamlit as st

from compliance_rag import config
from compliance_rag.storage.db import ensure_schema

st.set_page_config(page_title="Compliance Audits", layout="wide")

DB = config.DB_DSN
CLI = ["python", "-m", "compliance_rag.cli"]
RULE_PACKS_DIR = Path("rules")
UPLOAD_DIR = Path("uploads")

STATUS_COLORS = {"compliant": "🟢", "non_compliant": "🔴",
                 "missing_evidence": "🟡", "not_applicable": "⚪"}


@st.cache_resource
def init_db() -> None:
    ensure_schema()  # fresh database: create tables so the Overview page can load


init_db()


def query(sql: str, params: tuple = ()) -> pd.DataFrame:
    with psycopg.connect(DB) as cxn:
        return pd.read_sql(sql, cxn, params=params)  # type: ignore[arg-type]


def list_audits() -> pd.DataFrame:
    return query(
        "SELECT a.audit_id, a.pack_id, coalesce(d.filename, 'all documents') AS document, "
        "a.started_at, count(f.finding_id) AS findings "
        "FROM audits a LEFT JOIN documents d USING (doc_id) "
        "LEFT JOIN findings f USING (audit_id) "
        "GROUP BY 1,2,3,4 ORDER BY a.started_at DESC"
    )


def list_documents() -> pd.DataFrame:
    return query("SELECT doc_id, filename, domain FROM documents ORDER BY ingested_at DESC")


def audit_summary(audit_id: str) -> pd.DataFrame:
    return query("SELECT status, severity, count(*) AS n FROM findings "
                 "WHERE audit_id = %s GROUP BY 1,2 ORDER BY 1,2", (audit_id,))


def list_findings(audit_id: str, status: str | None, severity: str | None) -> pd.DataFrame:
    sql = ("SELECT f.finding_id, f.rule_id, r.description, f.status, f.severity, "
           "f.text, f.confidence, f.validated, count(c.citation_id) AS citations "
           "FROM findings f JOIN rules r USING (rule_id) "
           "LEFT JOIN citations c USING (finding_id) WHERE f.audit_id = %s")
    params: list = [audit_id]
    if status:
        sql += " AND f.status = %s"
        params.append(status)
    if severity:
        sql += " AND f.severity = %s"
        params.append(severity)
    sql += " GROUP BY 1,2,3,4,5,6,7,8 ORDER BY f.severity, f.rule_id"
    return query(sql, tuple(params))


def finding_citations(finding_id: str) -> pd.DataFrame:
    return query(
        "SELECT c.doc_id, d.filename, c.page, c.section, c.quote, c.match_score, "
        "k.text AS chunk_text "
        "FROM citations c JOIN documents d USING (doc_id) JOIN chunks k USING (chunk_id) "
        "WHERE c.finding_id = %s ORDER BY c.page", (finding_id,),
    )


def rule_coverage(audit_id: str) -> pd.DataFrame:
    return query(
        "SELECT r.rule_id, r.description, r.severity, count(f.finding_id) AS findings "
        "FROM rules r LEFT JOIN findings f ON f.rule_id = r.rule_id AND f.audit_id = %s "
        "GROUP BY 1,2,3 ORDER BY r.severity, r.rule_id", (audit_id,),
    )


st.sidebar.title("🛡️ Compliance Audits")
page = st.sidebar.radio("Navigate", ["Overview", "Findings", "Run Audit"])


def select_audit(label: str) -> str | None:
    audits = list_audits()
    if audits.empty:
        st.info("No audits yet. Go to **Run Audit** to create one.")
        return None
    options = [f"{r.audit_id} · {r.pack_id} · {r.document} · {r.started_at:%Y-%m-%d %H:%M}"
               for r in audits.itertuples()]
    choice = st.sidebar.selectbox(label, options, key=f"sel_{label}")
    return choice.split(" · ")[0]


def render_overview() -> None:
    st.header("Audit Overview")
    audit_id = select_audit("Audit")
    if not audit_id:
        return
    summary = audit_summary(audit_id)
    if summary.empty:
        st.warning("This audit has no findings recorded.")
        return
    total, by_status = int(summary.n.sum()), summary.groupby("status").n.sum()
    score = round(100 * by_status.get("compliant", 0) / total, 1) if total else 0

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Findings", total)
    col2.metric("Compliance score", f"{score}%")
    col3.metric("Non-compliant", int(by_status.get("non_compliant", 0)))
    col4.metric("Missing evidence", int(by_status.get("missing_evidence", 0)))

    st.subheader("Findings by status and severity")
    st.bar_chart(summary.pivot_table(index="severity", columns="status", values="n",
                                      fill_value=0, aggfunc="sum"))
    st.subheader("Rule coverage")
    st.dataframe(rule_coverage(audit_id), use_container_width=True, height=250)


def render_findings() -> None:
    st.header("Finding Explorer")
    audit_id = select_audit("Audit")
    if not audit_id:
        return
    col1, col2 = st.columns(2)
    status = col1.selectbox("Status", ["All", "compliant", "non_compliant",
                                      "missing_evidence", "not_applicable"])
    severity = col2.selectbox("Severity", ["All", "high", "medium", "low"])
    df = list_findings(audit_id, None if status == "All" else status,
                       None if severity == "All" else severity)
    if df.empty:
        st.info("No findings match these filters.")
        return
    for row in df.itertuples():
        title = (f"{STATUS_COLORS.get(row.status, '⚪')} "
                 f"{row.status.replace('_', ' ').title()} — {row.rule_id}")
        expander = st.expander(f"{title}  ({row.severity}, {row.citations} citations)")
        with expander:
            st.markdown(f"**Rule:** {row.description}")
            st.write(row.text)
            st.caption(f"confidence {row.confidence:.2f} · quotes validated: {row.validated}")
            cites = finding_citations(row.finding_id)
            if cites.empty:
                st.info("Absence claim: the required clause was not found in the document. "
                        "Verify the searches above before relying on it."
                        if row.status == "non_compliant"
                        else "No citations (abstained or not applicable).")
                continue
            st.markdown("**Evidence**")
            for cite in cites.itertuples():
                st.markdown(f"📄 **{cite.filename}** · page {cite.page} · §{cite.section} "
                            f"(match {cite.match_score:.0%})")
                st.markdown(f"> “{cite.quote}”")
                with st.expander("View full cited chunk"):
                    st.text(cite.chunk_text)


def run_cli(cmd: list[str], label: str) -> None:
    result = subprocess.run(CLI + cmd, capture_output=True, text=True)
    if result.returncode == 0:
        st.success(f"{label} completed")
        st.code(result.stdout)
    else:
        st.error(f"{label} failed")
        st.code(result.stderr or result.stdout)


def render_run_audit() -> None:
    st.header("Run a New Audit")
    st.subheader("1. Upload documents")
    uploads = st.file_uploader("PDF, DOCX, TXT/MD", accept_multiple_files=True,
                               type=["pdf", "docx", "txt", "md"])
    if uploads and st.button("Ingest uploaded documents"):
        UPLOAD_DIR.mkdir(exist_ok=True)
        for up in uploads:
            (UPLOAD_DIR / up.name).write_bytes(up.getvalue())
        run_cli(["ingest", str(UPLOAD_DIR)], "Ingestion")
    st.subheader("2. Choose a document")
    docs = list_documents()
    if docs.empty:
        st.warning("No documents ingested yet. Upload and ingest one above.")
        return
    doc_id = st.selectbox("Document", docs.doc_id,
                          format_func=lambda d: "{0} ({1})".format(
                              *docs.loc[docs.doc_id == d, ["filename", "domain"]].iloc[0]))
    st.subheader("3. Choose a rule pack")
    packs = sorted(RULE_PACKS_DIR.glob("*.yaml")) if RULE_PACKS_DIR.exists() else []
    if not packs:
        st.warning("No rule packs found in ./rules — add a YAML pack first.")
        return
    pack = st.selectbox("Rule pack", packs, format_func=lambda p: p.stem)
    if st.button("Preview rules"):
        run_cli(["audit", "rules", str(pack)], "Rule preview")
    st.subheader("4. Run the audit")
    if st.button(f"Run audit: {pack.name}"):
        run_cli(["audit", "run", str(pack), "--doc", doc_id], "Audit run")


if page == "Overview":
    render_overview()
elif page == "Findings":
    render_findings()
else:
    render_run_audit()
