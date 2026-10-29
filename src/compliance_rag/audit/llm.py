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


class QuotaExceeded(Exception):
    """The provider's quota is used up; further calls will fail until it resets."""


def retry_delay_s(exc: Exception) -> float | None:
    """Seconds to wait, from a provider message like 'Please retry in 11h47m47.5s'."""
    match = re.search(r"retry in ((?:\d+h)?(?:\d+m)?(?:[\d.]+s)?)", str(exc))
    if not match or not match.group(1):
        return None
    parts = ((r"(\d+)h", 3600), (r"(\d+)m(?!s)", 60), (r"([\d.]+)s", 1))
    return sum(float(m.group(1)) * k for p, k in parts if (m := re.search(p, match.group(1))))


def _duration(seconds: float) -> str:
    if seconds >= 3600:
        return f"{int(seconds // 3600)}h {int(seconds % 3600 // 60)}m"
    return f"{int(seconds // 60)}m" if seconds >= 60 else f"{int(seconds)}s"


def describe_quota(exc: Exception) -> str:
    """One readable line from a provider rate-limit error: which limit, and when it resets."""
    limit = re.search(r"description: (.+?)(?:\. |\.?$)", str(exc), re.M)
    delay = retry_delay_s(exc)
    return (f"{config.PROVIDER.capitalize()} quota reached: "
            f"{limit.group(1) if limit else 'rate limit exceeded'}."
            + (f" Resets in ~{_duration(delay)}." if delay else ""))


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
