"""Streamlit compliance audit dashboard. Run: streamlit run dashboard/app.py"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import psycopg
import streamlit as st
import yaml

from compliance_rag import config
from compliance_rag.audit.rules import SEVERITIES, custom_pack, dump_pack, load_rules
from compliance_rag.audit.runner import run_rules
from compliance_rag.ingestion.pipeline import SUPPORTED, ingest_file
from compliance_rag.storage.db import ensure_schema

st.set_page_config(page_title="Compliance Audits", layout="wide")

DB = config.DB_DSN
RULE_PACKS_DIR = Path("rules")
UPLOAD_DIR = Path("uploads")

STATUS_COLORS = {"compliant": "🟢", "non_compliant": "🔴",
                 "missing_evidence": "🟡", "not_applicable": "⚪"}

CUSTOM_STARTER = pd.DataFrame([
    {"label": "Liability cap specified", "severity": "high", "hints": "",
     "question": "Is liability capped at a stated amount or formula?"},
    {"label": "Governing law specified", "severity": "medium", "hints": "",
     "question": "Does the agreement state which jurisdiction's law governs it?"},
], columns=["label", "question", "severity", "hints"])


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


def render_finding_card(rule_id: str, description: str, status: str, severity: str,
                        text: str, confidence: float, citations: list[dict],
                        expanded: bool = False) -> None:
    """One finding with its evidence. Shared by the live audit view and the explorer."""
    title = (f"{STATUS_COLORS.get(status, '⚪')} {status.replace('_', ' ').title()} — "
             f"{rule_id}: {description}  ({severity}, {len(citations)} citations)")
    with st.expander(title, expanded=expanded):
        st.write(text)
        st.caption(f"confidence {confidence:.2f} · verified quotes: {len(citations)}")
        if not citations:
            st.info("Absence claim: the required clause was not found in the document. "
                    "Check the searches listed above before relying on it."
                    if status == "non_compliant"
                    else "No citations (abstained or not applicable).")
            return
        st.markdown("**Evidence**")
        for i, cite in enumerate(citations):
            source = f"📄 **{cite['filename']}** · " if cite.get("filename") else "📄 "
            st.markdown(f"{source}page {cite['page']} · §{cite['section']} "
                        f"(match {cite['match_score']:.0%})")
            st.markdown(f"> “{cite['quote']}”")
            if cite.get("chunk_text"):
                with st.popover("View full cited passage"):
                    st.text(cite["chunk_text"])


st.sidebar.title("🛡️ Compliance Audits")
page = st.sidebar.radio("Navigate", ["Run Audit", "Overview", "Findings"])


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
    st.dataframe(rule_coverage(audit_id), width="stretch", height=250)


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
        render_finding_card(row.rule_id, row.description, row.status, row.severity,
                            row.text, row.confidence,
                            finding_citations(row.finding_id).to_dict("records"))


# ---------- Run Audit: upload → checklist → live results ----------

def ingest_uploads(uploads: list) -> None:
    """Index each newly uploaded file once; remember outcomes across reruns."""
    seen: dict[str, str] = st.session_state.setdefault("ingested_uploads", {})
    for up in uploads or []:
        key = f"{up.name}:{up.size}"
        if key in seen and not seen[key].startswith("error:"):
            continue
        if key in seen:  # failed earlier: offer a retry instead of re-trying every rerun
            st.error(f"Could not index {up.name}: {seen[key][6:]}")
            if st.button("Retry", key=f"retry_{key}"):
                del seen[key]
                st.rerun()
            continue
        UPLOAD_DIR.mkdir(exist_ok=True)
        path = UPLOAD_DIR / up.name
        path.write_bytes(up.getvalue())
        try:
            with st.spinner(f"Indexing {up.name}…"):
                result = ingest_file(path)
        except Exception as exc:  # noqa: BLE001 — surface any failure in the UI
            seen[key] = f"error:{exc}"
            st.rerun()
        if result.status == "unreadable":
            seen[key] = "unreadable"
            st.warning(f"{up.name}: no extractable text (scanned PDFs aren't supported yet).")
            continue
        seen[key] = result.doc.doc_id
        st.session_state["selected_doc"] = result.doc.doc_id
        if result.status == "already_indexed":
            st.info(f"{up.name} was already indexed. Selected it below.")
        else:
            st.success(f"Indexed {up.name}: {result.chunks} passages.")


def choose_document() -> tuple[str, str] | None:
    st.subheader("1. Document")
    uploads = st.file_uploader("Upload a contract, policy or report",
                               type=[s.lstrip(".") for s in SUPPORTED],
                               accept_multiple_files=True)
    ingest_uploads(uploads)
    docs = list_documents()
    if docs.empty:
        st.info("Upload a document to begin.")
        return None
    ids = list(docs.doc_id)
    names = dict(zip(docs.doc_id, docs.filename))
    selected = st.session_state.get("selected_doc")
    doc_id = st.selectbox("Document to audit", ids,
                          index=ids.index(selected) if selected in ids else 0,
                          format_func=lambda d: names[d])
    return doc_id, names[doc_id]


@st.cache_data
def pack_title(path: Path, mtime: float) -> str:
    meta = yaml.safe_load(path.read_text()).get("meta", {})
    return meta.get("name") or path.stem


def choose_template() -> tuple[str, str, list] | None:
    packs = sorted(RULE_PACKS_DIR.glob("*.yaml")) if RULE_PACKS_DIR.exists() else []
    if not packs:
        st.warning("No templates found in ./rules.")
        return None
    pack = st.selectbox("Template", packs,
                        format_func=lambda p: pack_title(p, p.stat().st_mtime))
    pack_id, version, rules = load_rules(pack)
    with st.expander(f"{len(rules)} checks in this template"):
        st.dataframe(pd.DataFrame([{"check": r.description, "severity": r.severity,
                                    "rule": r.rule_id} for r in rules]),
                     hide_index=True, width="stretch")
    return pack_id, version, rules


def build_custom_checklist() -> tuple[str, str, list] | None:
    name = st.text_input("Checklist name", value="My checklist")
    edited = st.data_editor(
        CUSTOM_STARTER, num_rows="dynamic", hide_index=True, width="stretch",
        key="custom_items",
        column_config={
            "label": st.column_config.TextColumn(
                "Check", required=True, help="Short name, e.g. 'Liability cap specified'"),
            "question": st.column_config.TextColumn(
                "What to verify", required=True, width="large",
                help="Phrase it as a test with a clear pass condition."),
            "severity": st.column_config.SelectboxColumn(
                "Severity", options=list(SEVERITIES), default="medium", required=True),
            "hints": st.column_config.TextColumn(
                "Search phrases (optional)",
                help="';'-separated wording as it would appear in the document. "
                     "Defaults to the check and question."),
        },
    )
    items = [{k: v if isinstance(v, str) else "" for k, v in row.items()}
             for row in edited.to_dict("records")]
    items = [i for i in items if i["label"].strip() and i["question"].strip()]
    if not items:
        st.info("Add at least one row with a check and what to verify.")
        return None
    pack_id, rules = custom_pack(name, items)
    if st.button("Save as template", help=f"Writes rules/{pack_id}.yaml"):
        path = RULE_PACKS_DIR / f"{pack_id}.yaml"
        existed = path.exists()
        path.write_text(dump_pack(pack_id, name, "0.1.0", rules))
        st.success(f"{'Updated' if existed else 'Saved'} template rules/{path.name}. "
                   "It now appears under Template.")
    return pack_id, "custom", rules


def stream_audit(doc_id: str, filename: str, pack_id: str, version: str, rules: list) -> None:
    """Run the checklist and render each finding the moment its rule completes."""
    total = len(rules)
    bar = st.progress(0.0, text=f"0 / {total} checks")
    tally = st.empty()
    results = st.container()
    counts = dict.fromkeys(STATUS_COLORS, 0)
    done = 0

    def on_progress(rule, event, detail):
        nonlocal done
        done += 1
        bar.progress(done / total, text=f"{done} / {total} checks")
        if event == "dropped":
            with results:
                st.error(f"⚠️ {rule.rule_id}: {rule.description}. Check failed: {detail}")

    def on_finding(rule, finding, citations):
        counts[finding["status"]] = counts.get(finding["status"], 0) + 1
        tally.markdown("  ·  ".join(f"{STATUS_COLORS.get(s, '⚪')} {s.replace('_', ' ')}: **{n}**"
                                    for s, n in counts.items()))
        with results:
            render_finding_card(rule.rule_id, rule.description, finding["status"],
                                finding.get("severity", rule.severity), finding["finding"],
                                float(finding.get("confidence", 0.5)),
                                [dict(c, filename=filename) for c in citations],
                                expanded=finding["status"] != "compliant")

    try:
        audit_id, _ = run_rules(pack_id, version, rules, on_progress, doc_id, on_finding)
    except Exception as exc:  # noqa: BLE001 — e.g. Qdrant or Postgres unreachable
        st.error(f"Audit stopped: {exc}")
        return
    bar.progress(1.0, text=f"Audit complete: {total} checks")
    st.success(f"Saved as {audit_id}. Revisit it any time under **Overview** or **Findings**.")


def render_run_audit() -> None:
    st.header("Run an Audit")
    doc = choose_document()
    if not doc:
        return
    st.subheader("2. Checklist")
    mode = st.radio("Checklist source", ["Template", "Custom"], horizontal=True,
                    label_visibility="collapsed")
    checklist = choose_template() if mode == "Template" else build_custom_checklist()
    if not checklist:
        return
    st.subheader("3. Results")
    pack_id, version, rules = checklist
    if st.button(f"Run {len(rules)} checks on {doc[1]}", type="primary"):
        stream_audit(*doc, pack_id, version, rules)


if page == "Overview":
    render_overview()
elif page == "Findings":
    render_findings()
else:
    render_run_audit()
