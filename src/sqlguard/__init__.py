"""sqlguard: the SQL firewall for LLM apps.

Validate SQL written by an untrusted source and rewrite it so it is a single read-only query
over allowlisted tables and columns, filtered per user and bounded in rows::

    from sqlguard import Guard, Policy

    guard = Guard(Policy(dialect="postgres", tables={"invoices"}, max_rows=1000))
    result = guard.check("SELECT * FROM invoices")
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from sqlguard.guard import Guard
from sqlguard.policy import FunctionPolicy, Policy
from sqlguard.result import CheckResult, GuardViolation, PolicyError, Violation, ViolationCode

try:
    __version__ = version("sqlguardllm")
except PackageNotFoundError:  # pragma: no cover - running from a source tree without install
    __version__ = "0.0.0"

__all__ = [
    "CheckResult",
    "FunctionPolicy",
    "Guard",
    "GuardViolation",
    "Policy",
    "PolicyError",
    "Violation",
    "ViolationCode",
    "__version__",
]
