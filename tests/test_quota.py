"""Quota handling: parsing provider limits, waiting out short ones, stopping on long ones.

Retrieval, the LLM and the database are faked; no DB or API needed.
"""
import litellm
import pytest

from compliance_rag.audit import runner
from compliance_rag.audit.llm import describe_quota, retry_delay_s
from compliance_rag.audit.rules import Rule

DAILY = ("Quota exceeded for metric: generativelanguage.googleapis.com/"
         "generate_content_free_tier_requests, limit: 20, model: gemini-2.5-flash, "
         "description: Generate content requests per day limit exceeded on Free Tier. "
         "See https://ai.google.dev/gemini-api/docs/rate-limits.\n"
         "Please retry in 11h47m47.587872339s.")
PER_MINUTE = ("description: Generate content requests per minute limit exceeded. "
              "Please retry in 2.5s.")


def rate_limit(message: str) -> litellm.RateLimitError:
    return litellm.RateLimitError(message, llm_provider="gemini", model="gemini-2.5-flash")


def test_retry_delay_parsing():
    assert retry_delay_s(Exception(DAILY)) == pytest.approx(11 * 3600 + 47 * 60 + 47.59, abs=0.01)
    assert retry_delay_s(Exception(PER_MINUTE)) == 2.5
    assert retry_delay_s(Exception("429 Too Many Requests")) is None


def test_describe_quota_names_limit_and_reset():
    text = describe_quota(Exception(DAILY))
    assert "requests per day limit exceeded on Free Tier" in text
    assert "Resets in ~11h 47m" in text


class FakeCursor:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self.log.append((sql, params))


class FakeConn:
    def __init__(self):
        self.log: list = []

    def cursor(self):
        return FakeCursor(self.log)

    def commit(self):
        pass

    def close(self):
        pass


@pytest.fixture
def fake_backend(monkeypatch):
    conn = FakeConn()
    monkeypatch.setattr(runner, "ensure_schema", lambda: None)
    monkeypatch.setattr(runner, "get_conn", lambda: conn)
    monkeypatch.setattr(runner, "hybrid_search", lambda *a: ["c1"])
    monkeypatch.setattr(runner, "fetch_chunks", lambda ids: [])
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    return conn


RULES = [Rule(f"R{i}", "legal", f"rule {i}", "high", "check", ["hint"], True) for i in range(4)]
NOT_APPLICABLE = {"status": "not_applicable", "finding": "n/a", "confidence": 0.9, "citations": []}


def test_daily_quota_stops_the_audit(fake_backend, monkeypatch):
    calls = iter([NOT_APPLICABLE, rate_limit(DAILY)])

    def generate(rule, chunks):
        result = next(calls)
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(runner, "generate_finding", generate)

    events = []
    _, summary = runner.run_rules("p", "1", RULES, lambda r, e, d: events.append((r.rule_id, e)))
    assert events == [("R0", "not_applicable"), ("R1", "quota")]  # R2, R3 never attempted
    assert summary["not_run"] == 3 and summary["dropped"] == 0
    assert "Resets in ~11h" in summary["stopped"]
    sql, params = fake_backend.log[-1]
    assert "stopped_reason" in sql and "Resets in ~11h" in params[0]


def test_per_minute_limit_is_waited_out(fake_backend, monkeypatch):
    calls = iter([rate_limit(PER_MINUTE)] + [NOT_APPLICABLE] * len(RULES))

    def generate(rule, chunks):
        result = next(calls)
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(runner, "generate_finding", generate)

    _, summary = runner.run_rules("p", "1", RULES)
    assert summary["not_applicable"] == 4 and "stopped" not in summary
