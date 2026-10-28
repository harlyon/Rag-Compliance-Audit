"""Finding generation via hosted LLM (LiteLLM — provider-agnostic)."""
from __future__ import annotations

import json
import re

from litellm import completion
from litellm.types.utils import Choices, ModelResponse

from compliance_rag import config
from compliance_rag.audit.rules import Rule
from compliance_rag.security.redaction import redact

SYSTEM_PROMPT = """You are a compliance audit assistant. You judge ONLY from the
evidence provided. You must quote evidence verbatim. Allowed statuses:
- compliant: rule clearly satisfied by quoted evidence
- non_compliant: rule violated or required clause absent from the evidence
- missing_evidence: evidence retrieved is insufficient to judge
- not_applicable: evidence shows the rule does not apply to this document type
Never invent quotes. If status is missing_evidence or not_applicable, citations may be empty.
Respond with ONLY a JSON object, no markdown fences:
{
  "status": "...", "severity": "...", "finding": "one-paragraph explanation",
  "confidence": 0.0-1.0,
  "citations": [{"chunk_id": "...", "quote": "exact verbatim span from that chunk"}]
}"""


def build_user_prompt(rule: Rule, chunks: list[dict]) -> str:
    evidence_blocks = [
        f'<evidence chunk_id="{c["chunk_id"]}" doc="{c["doc_id"]}" '
        f'page="{c["page"]}" section="{c["section"]}">\n{redact(c["text"])}\n</evidence>'
        for c in chunks
    ]
    return (
        f"RULE {rule.rule_id} ({rule.severity}): {rule.description}\n"
        f"Check: {rule.check_prompt}\n\n"
        + "\n".join(evidence_blocks)
        + "\n\nReturn the finding JSON now."
    )


def parse_json_block(raw: str) -> dict:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError("LLM returned no JSON object")
    return json.loads(match.group(0))


def generate_finding(rule: Rule, chunks: list[dict]) -> dict:
    resp = completion(
        model=config.LLM_MODEL,
        temperature=config.TEMPERATURE,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(rule, chunks)},
        ],
    )
    # completion() is typed to also return stream/async variants; we use neither.
    if not isinstance(resp, ModelResponse) or not isinstance(resp.choices[0], Choices):
        raise TypeError(f"Expected a non-streaming ModelResponse, got {type(resp).__name__}")
    return parse_json_block(resp.choices[0].message.content or "")
