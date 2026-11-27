"""Every corpus case over the stdio engine (a child process) must equal the in-process answer."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from export_corpus import OUT

DATA = json.loads(OUT.read_text(encoding="utf-8"))
GROUPS: list[dict[str, Any]] = DATA["groups"]

# SQLGUARD_ENGINE_BIN runs the same corpus against a standalone engine binary (see
# docs/PROTOCOL.md): the executable, or the one-dir folder that holds it.
ENGINE_BIN = os.environ.get("SQLGUARD_ENGINE_BIN")


def engine_command() -> list[str]:
    if not ENGINE_BIN:
        return [sys.executable, "-m", "sqlguardllm", "engine", "--stdio"]
    path = Path(ENGINE_BIN)
    if path.is_dir():
        names = ("sqlguardllm-engine", "sqlguardllm-engine.exe")
        path = next(p for p in (path / n for n in names) if p.is_file())
    return [str(path), "--stdio"]


@pytest.fixture(params=GROUPS, ids=[g["name"] for g in GROUPS])
def engine(
    request: pytest.FixtureRequest,
) -> Iterator[tuple[dict[str, Any], subprocess.Popen[str]]]:
    proc = subprocess.Popen(
        engine_command(),
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


def test_hello() -> None:
    import sqlguardllm  # noqa: PLC0415

    proc = subprocess.run(
        engine_command(),
        input='{"id": 1, "type": "hello"}\n',
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert proc.returncode == 0
    response = json.loads(proc.stdout.splitlines()[0])
    assert response["type"] == "hello" and response["protocol"] == 1
    assert response["engine_version"] == sqlguardllm.__version__
    assert "postgres" in response["dialects"]


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
