"""Rule pack loading (version-controlled YAML)."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

SEVERITIES = ("high", "medium", "low")
CUSTOM_CHECK = (
    "{question}\n"
    "Quote the exact clause(s) that answer this. If the document clearly satisfies "
    "this item, mark compliant. If the required content is absent or inadequate, "
    "mark non_compliant. If the item does not apply to this kind of document, "
    "mark not_applicable."
)


@dataclass
class Rule:
    rule_id: str
    domain: str
    description: str
    severity: str
    check_prompt: str
    retrieval_hints: list[str]
    evidence_required: bool


def load_rules(pack_path: Path) -> tuple[str, str, list[Rule]]:
    data = yaml.safe_load(pack_path.read_text())
    meta = data["meta"]
    rules = [
        Rule(
            rule_id=r["rule_id"],
            domain=r["domain"],
            description=r["description"],
            severity=r["severity"],
            check_prompt=r["check_prompt"],
            retrieval_hints=r.get("retrieval_hints", [r["description"]]),
            evidence_required=r.get("evidence_required", True),
        )
        for r in data["rules"]
    ]
    return meta["pack_id"], str(meta["version"]), rules


def custom_pack(name: str, items: list[dict]) -> tuple[str, list[Rule]]:
    """Build a pack from checklist items {label, question, severity?, hints?}.

    `hints` is an optional ';'-separated list of search phrases; it defaults to
    the label and question. Returns (pack_id, rules).
    """
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "custom"
    rules = []
    for i, item in enumerate(items, start=1):
        label, question = item["label"].strip(), item["question"].strip()
        hints = [h.strip() for h in (item.get("hints") or "").split(";") if h.strip()]
        severity = item.get("severity") or "medium"
        if severity not in SEVERITIES:
            raise ValueError(f"severity must be one of {SEVERITIES}, got {severity!r}")
        rules.append(Rule(
            rule_id=f"CUSTOM-{slug.upper()}-{i:03d}",
            domain="general",
            description=label,
            severity=severity,
            check_prompt=CUSTOM_CHECK.format(question=question),
            retrieval_hints=hints or [label, question],
            evidence_required=True,
        ))
    return f"custom_{slug}", rules


def dump_pack(pack_id: str, name: str, version: str, rules: list[Rule]) -> str:
    """Serialise a pack to the same YAML format load_rules reads."""
    data = {"meta": {"pack_id": pack_id, "name": name, "version": version},
            "rules": [asdict(r) for r in rules]}
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=88)
