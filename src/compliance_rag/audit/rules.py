"""Rule pack loading (version-controlled YAML)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


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
