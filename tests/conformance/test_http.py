"""Every corpus case over HTTP must equal the in-process answer (the server adds nothing)."""

from __future__ import annotations

import json
import threading
import urllib.request
from collections.abc import Iterator
from typing import Any

import pytest
from export_corpus import OUT, render

from sqlguardllm import Guard, Policy
from sqlguardllm.server import make_server

DATA = json.loads(OUT.read_text(encoding="utf-8"))
GROUPS: list[dict[str, Any]] = DATA["groups"]


def test_cases_json_is_up_to_date() -> None:
    assert OUT.read_text(encoding="utf-8") == render(), (
        "run: uv run python tests/conformance/export_corpus.py"
    )


@pytest.fixture(params=GROUPS, ids=[g["name"] for g in GROUPS])
def served(request: pytest.FixtureRequest) -> Iterator[tuple[dict[str, Any], str]]:
    group = request.param
    server = make_server(Guard(Policy.from_dict(group["policy"])), port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield group, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_group_over_http(served: tuple[dict[str, Any], str]) -> None:
    group, url = served
    for case in group["cases"]:
        body = json.dumps({"sql": case["sql"], "context": case["context"]}).encode()
        req = urllib.request.Request(f"{url}/v1/check", body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as response:
            got = json.load(response)
        codes = sorted({v["code"] for v in got["violations"]})
        actual = {"ok": got["ok"], "sql": got["sql"], "codes": codes}
        assert actual == case["expected"], case["id"]
        assert (case["expect"] == "allow") == got["ok"], case["id"]
