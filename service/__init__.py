"""HTTP service layer for Agent QA (Step 2).

A thin FastAPI wrapper over :func:`core.report.evaluate`. The HTTP layer only
validates the request and delegates; all reliability logic lives in ``core``.
"""

__version__ = "0.1.0"
