"""Unit tests for the anti-hallucination gate. No DB or API needed.
Run:  pytest tests/"""
import pytest

from compliance_rag.audit.validate import quote_is_verbatim, validate_finding

CHUNKS = [{
    "chunk_id": "c1", "doc_id": "doc_1", "page": 2, "section": "4. Remuneration",
    "chunk_type": "clause",
    "text": "The employee shall be paid a monthly salary of [REDACTED] "
            "payable on the last working day of each month.",
}]


def test_exact_quote_scores_one():
    score = quote_is_verbatim("paid a monthly salary", CHUNKS[0]["text"])
    assert score == 1.0


def test_typographic_variants_pass():
    # case, whitespace and curly quotes/dashes never change meaning
    score = quote_is_verbatim("Paid a  MONTHLY salary", CHUNKS[0]["text"])
    assert score == 1.0


def test_single_char_typo_passes_threshold():
    score = quote_is_verbatim("payable on the last working dya of each month",
                              CHUNKS[0]["text"])
    assert score >= 0.95


def test_elided_quote_passes():
    score = quote_is_verbatim("monthly salary of [REDACTED] ... last working day",
                              CHUNKS[0]["text"])
    assert score == 1.0


def test_elided_quote_out_of_order_fails():
    score = quote_is_verbatim("last working day ... monthly salary", CHUNKS[0]["text"])
    assert score < 0.95


def test_dropped_words_fail():
    # an omitted word can flip meaning ("shall not exceed" -> "shall exceed")
    text = "The restraint period shall not exceed twelve months after termination."
    assert quote_is_verbatim("shall exceed twelve months", text) < 0.95
    score = quote_is_verbatim("monthly salary of [REDACTED] payable on the last day",
                              CHUNKS[0]["text"])
    assert score < 0.95


def test_quote_of_redacted_text_matches_raw_chunk():
    # the LLM sees redacted evidence; the stored chunk is the original
    raw = "The employee shall be paid a monthly salary of R 25,000.00 payable monthly."
    assert quote_is_verbatim("paid a monthly salary of [REDACTED] payable", raw) == 1.0


def test_fabricated_quote_fails():
    score = quote_is_verbatim("the employee gets company shares quarterly", CHUNKS[0]["text"])
    assert score < 0.95


def test_valid_citation_is_kept():
    finding = {"status": "non_compliant", "finding": "missing hours",
               "confidence": 0.9,
               "citations": [{"chunk_id": "c1",
                              "quote": "paid a monthly salary of [REDACTED]"}]}
    result, citations, validated = validate_finding(finding, CHUNKS)
    assert validated and len(citations) == 1
    assert citations[0]["doc_id"] == "doc_1" and citations[0]["page"] == 2


def test_fabricated_chunk_reference_dropped():
    finding = {"status": "non_compliant", "finding": "x", "confidence": 0.9,
               "citations": [{"chunk_id": "does-not-exist", "quote": "anything"}]}
    result, citations, validated = validate_finding(finding, CHUNKS)
    assert citations == [] and not validated
    # a violation backed only by fabricated evidence is unverified, not dropped
    assert result["status"] == "missing_evidence" and result["confidence"] <= 0.4


def test_non_compliant_without_citations_is_absence_claim():
    finding = {"status": "non_compliant", "finding": "No liability cap clause.",
               "confidence": 0.8, "citations": []}
    result, citations, validated = validate_finding(finding, CHUNKS)
    assert result["status"] == "non_compliant" and result["absence"]
    assert citations == [] and not validated


def test_ungrounded_compliant_downgraded():
    finding = {"status": "compliant", "finding": "all good", "confidence": 0.9,
               "citations": []}
    result, citations, validated = validate_finding(finding, CHUNKS)
    assert result["status"] == "missing_evidence"
    assert result["confidence"] <= 0.4


def test_redaction_keeps_words_ending_in_r():
    from compliance_rag.security.redaction import redact
    assert redact("The EMPLOYER shall pay R 25,000.00 monthly") == \
        "The EMPLOYER shall pay [REDACTED] monthly"
    assert redact("ZAR1500 per month") == "[REDACTED] per month"


def test_custom_pack_round_trips_through_yaml(tmp_path):
    from compliance_rag.audit.rules import custom_pack, dump_pack, load_rules
    pack_id, rules = custom_pack("Vendor NDA!", [
        {"label": "Term stated", "question": "Is the NDA term stated?", "severity": "high",
         "hints": "term of this agreement; duration"},
        {"label": "Return of materials", "question": "Must materials be returned?",
         "severity": "", "hints": ""},
    ])
    assert pack_id == "custom_vendor_nda"
    assert [r.rule_id for r in rules] == ["CUSTOM-VENDOR_NDA-001", "CUSTOM-VENDOR_NDA-002"]
    assert rules[0].retrieval_hints == ["term of this agreement", "duration"]
    assert rules[1].severity == "medium"
    assert rules[1].retrieval_hints == ["Return of materials", "Must materials be returned?"]
    path = tmp_path / "p.yaml"
    path.write_text(dump_pack(pack_id, "Vendor NDA!", "0.1.0", rules))
    assert load_rules(path) == (pack_id, "0.1.0", rules)


def test_custom_pack_rejects_bad_severity():
    from compliance_rag.audit.rules import custom_pack
    with pytest.raises(ValueError):
        custom_pack("x", [{"label": "a", "question": "b", "severity": "urgent"}])


@pytest.mark.parametrize("line,is_heading", [
    ("7 Termination", True), ("7.2 Termination", True), ("7. Termination", True),
    ("Clause 12 Confidentiality", True), ("Section 4(a) Payment", True),
    ("The Supplier shall supply the goods.", False), ("2024", False),
])
def test_heading_pattern(line, is_heading):
    from compliance_rag import config
    assert bool(config.HEADING_RE.match(line)) is is_heading
