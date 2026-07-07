"""Shared test fixtures.

Keep the suite hermetic: no test should reach a live reputation memory sidecar
by accident. This disables the memory client by default, so ``remember`` and
``recall`` are no-ops unless a test explicitly opts in by setting
``core.reputation.MEMORY_SVC_URL`` itself.
"""

import pytest

import core.reputation as reputation


@pytest.fixture(autouse=True)
def _isolate_reputation_memory(monkeypatch):
    monkeypatch.setattr(reputation, "MEMORY_SVC_URL", "")
