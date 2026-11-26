"""sqlguardllm: the SQL firewall for LLM apps.

Validate SQL written by an untrusted source and rewrite it so it is a single read-only query
over allowlisted tables and columns, filtered per user and bounded in rows::

    from sqlguardllm import Guard, Policy

    guard = Guard(Policy(dialect="postgres", tables={"invoices"}, max_rows=1000))
    result = guard.check("SELECT * FROM invoices")
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from sqlguardllm.explain import Explanation
from sqlguardllm.guard import Guard
from sqlguardllm.policy import FunctionPolicy, Policy
from sqlguardllm.policy_set import PolicySet
from sqlguardllm.result import CheckResult, GuardViolation, PolicyError, Violation, ViolationCode

try:
    __version__ = version("sqlguardllm")
except PackageNotFoundError:  # pragma: no cover - running from a source tree without install
    __version__ = "0.0.0"

__all__ = [
    "CheckResult",
    "Explanation",
    "FunctionPolicy",
    "Guard",
    "GuardViolation",
    "Policy",
    "PolicyError",
    "PolicySet",
    "Violation",
    "ViolationCode",
    "__version__",
]
