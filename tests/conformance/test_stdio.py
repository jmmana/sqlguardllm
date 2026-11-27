"""Every corpus case over the stdio engine (a child process) must equal the in-process answer."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from typing import Any

import pytest
from export_corpus import OUT

DATA = json.loads(OUT.read_text(encoding="utf-8"))
GROUPS: list[dict[str, Any]] = DATA["groups"]


@pytest.fixture(params=GROUPS, ids=[g["name"] for g in GROUPS])
def engine(
    request: pytest.FixtureRequest,
) -> Iterator[tuple[dict[str, Any], subprocess.Popen[str]]]:
    proc = subprocess.Popen(
        [sys.executable, "-m", "sqlguardllm", "engine", "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
    )
    yield request.param, proc
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.close()  # EOF must stop the engine
    assert proc.wait(timeout=30) == 0
    proc.stdout.close()


def test_group_over_stdio(engine: tuple[dict[str, Any], subprocess.Popen[str]]) -> None:
    group, proc = engine
    assert proc.stdin is not None and proc.stdout is not None

    def call(message: dict[str, Any]) -> dict[str, Any]:
        proc.stdin.write(json.dumps(message) + "\n")  # type: ignore[union-attr]
        proc.stdin.flush()  # type: ignore[union-attr]
        return json.loads(proc.stdout.readline())  # type: ignore[union-attr]

    handle = call({"id": 0, "type": "load_policy", "policy": group["policy"]})["handle"]
    for number, case in enumerate(group["cases"], start=1):
        message = {
            "id": number,
            "type": "check",
            "handle": handle,
            "sql": case["sql"],
            "context": case["context"],
        }
        response = call(message)
        assert response["id"] == number, case["id"]
        got = response["result"]
        codes = sorted({v["code"] for v in got["violations"]})
        assert {"ok": got["ok"], "sql": got["sql"], "codes": codes} == case["expected"], case["id"]
        assert (case["expect"] == "allow") == got["ok"], case["id"]
