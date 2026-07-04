"""Tests for the FastAPI service layer (Step 2).

The engine itself is covered elsewhere; here we verify the HTTP contract only,
with ``core.report.evaluate`` patched so the tests are fast and deterministic.
"""

import core.report as report_mod
import service.app as app_mod
from core.connect import connection_failed_result, connection_success_result
from core.models import CATEGORY_CONNECTION, CheckResult
from core.report import assemble_report
from fastapi.testclient import TestClient

client = TestClient(app_mod.app)


def _healthy_report(url: str):
    conn = connection_success_result("streamable-http", 1)
    latency = CheckResult("latency", "round-trip", True, 100.0, "fast")
    return assemble_report(url, conn, latency, tools=[])


def test_health_ok():
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "agent-qa"


def test_evaluate_returns_report(monkeypatch):
    async def fake_evaluate(url):
        return _healthy_report(url)

    monkeypatch.setattr(app_mod, "evaluate", fake_evaluate)

    resp = client.post("/evaluate", json={"endpoint_url": "https://good.example/mcp"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["url"] == "https://good.example/mcp"
    assert body["reachable"] is True
    assert body["grade"] in {"A", "B", "C", "D", "F"}
    assert "category_scores" in body


def test_evaluate_unreachable_is_200_with_grade_f(monkeypatch):
    async def fake_evaluate(url):
        report = assemble_report(
            url, connection_failed_result("no route"), latency=None, tools=[]
        )
        report.error = "no route"
        return report

    monkeypatch.setattr(app_mod, "evaluate", fake_evaluate)

    resp = client.post("/evaluate", json={"endpoint_url": "https://dead.example/mcp"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["reachable"] is False
    assert body["grade"] == "F"
    assert body["error"] == "no route"


def test_evaluate_rejects_non_http_url():
    resp = client.post("/evaluate", json={"endpoint_url": "ftp://nope"})
    assert resp.status_code == 422


def test_evaluate_rejects_missing_url():
    resp = client.post("/evaluate", json={})
    assert resp.status_code == 422
