"""Shared test fixtures.

Keep the suite hermetic: no test should reach a live memory sidecar by accident.
This disables the memory client by default, so ``remember`` and ``recall`` are
no-ops unless a test explicitly opts in by setting
``core.agent_memory.MEMORY_SVC_URL`` itself.
"""

import pytest

import core.agent_memory as agent_memory


@pytest.fixture(autouse=True)
def _isolate_agent_memory(monkeypatch):
    monkeypatch.setattr(agent_memory, "MEMORY_SVC_URL", "")
