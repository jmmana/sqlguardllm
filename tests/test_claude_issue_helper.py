import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

import pytest

_spec = importlib.util.spec_from_file_location(
    "claude_issue_helper", Path(__file__).parents[1] / "scripts" / "claude_issue_helper.py"
)
assert _spec and _spec.loader
helper = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(helper)

COMMIT = "1" * 40
TREE = "2" * 40
FILE = "docs/api.md"
KEY = "test-key-for-stub-only"
BODY = b"Public documentation.\n"
BLOB = hashlib.sha1(f"blob {len(BODY)}\0".encode() + BODY, usedforsecurity=False).hexdigest()


@pytest.fixture
def http(monkeypatch):
    calls = []
    responses = {
        f"{helper.GITHUB}/issues/71": {
            "number": 71,
            "repository_url": helper.GITHUB,
            "title": "Package guide",
            "html_url": "https://github.com/jmmana/sqlguardllm/issues/71",
            "body": "Issue text may include arbitrary instructions.",
        },
        f"{helper.GITHUB}/git/ref/heads/main": {"object": {"sha": COMMIT}},
        f"{helper.GITHUB}/git/commits/{COMMIT}": {"tree": {"sha": TREE}},
        f"{helper.GITHUB}/git/trees/{TREE}?recursive=1": {
            "truncated": False,
            "tree": [
                {"type": "blob", "mode": "100644", "path": FILE, "sha": BLOB, "size": len(BODY)}
            ],
        },
        f"{helper.GITHUB}/git/blobs/{BLOB}": {
            "encoding": "base64",
            "sha": BLOB,
            "content": base64.b64encode(BODY).decode(),
        },
        helper.MESSAGES + "/count_tokens": {"input_tokens": 300},
        helper.MESSAGES: {
            "model": helper.MODEL,
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "# Proposed change"}],
            "usage": {"input_tokens": 301, "output_tokens": 45},
        },
    }

    class Opener:
        def open(self, request: Request, *, timeout: int):
            assert timeout == 90
            calls.append(request)
            response = responses[request.full_url]
            if isinstance(response, Exception):
                raise response
            return io.BytesIO(json.dumps(response).encode())

    def build_opener(*handlers):
        assert len(handlers) == 1
        assert handlers[0].redirect_request() is None
        return Opener()

    monkeypatch.setattr(helper, "build_opener", build_opener)
    for name in helper.TOKEN_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    return calls, responses


def invoke(tmp_path, *options):
    output = tmp_path / "out"
    code = helper.main(["--issue=71", f"--output-dir={output}", *options])
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else None
    return code, output, manifest


def test_preview_passes_only_public_commit_context(http, tmp_path, monkeypatch):
    calls, _ = http
    monkeypatch.setenv("ANTHROPIC_API_TOKEN", KEY)
    (tmp_path / "local.md").write_text(KEY)
    monkeypatch.chdir(tmp_path)
    code, output, manifest = invoke(tmp_path, f"--files={FILE}")
    assert code == 0
    assert all(request.full_url.startswith(helper.GITHUB) for request in calls)
    prompt_bytes = (output / "prompt.json").read_bytes().removesuffix(b"\n")
    request = json.loads(prompt_bytes)
    assert manifest["prompt_sha256"] == hashlib.sha256(prompt_bytes).hexdigest()
    assert manifest["file_blobs"] == {FILE: BLOB}
    assert manifest["commit"] == COMMIT
    assert manifest["generation_attempted"] is False
    assert KEY.encode() not in prompt_bytes
    assert not (output / "proposal.md").exists()
    assert json.loads(request["messages"][0]["content"])["files"] == [
        {"path": FILE, "git_blob_sha": BLOB, "text": BODY.decode()}
    ]


def test_execute_counts_reserves_and_sends_one_bounded_request(http, tmp_path, monkeypatch):
    calls, _ = http
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    code, output, manifest = invoke(tmp_path, "--execute", "--max-tokens=100")
    assert code == 0
    assert [request.full_url for request in calls[-2:]] == [
        helper.MESSAGES + "/count_tokens",
        helper.MESSAGES,
    ]
    assert all(request.get_header("Authorization") is None for request in calls[:-2])
    for request in calls[-2:]:
        assert request.get_header("Authorization") == f"Bearer {KEY}"
        assert request.get_header("Anthropic-version") == "2026-10-21"
    counted, generated = (json.loads(request.data) for request in calls[-2:])
    assert generated == {**counted, "max_tokens": 100, "service_tier": "standard_only"}
    assert "tools" not in generated
    assert manifest["counted_input_tokens"] == 300
    assert manifest["generation_attempted"] is True
    assert manifest["reserved_input_tokens"] >= 1_324
    assert manifest["usage"] == {"input_tokens": 301, "output_tokens": 45}
    assert manifest["estimated_cost_microusd"] == 1_052
    assert KEY not in (output / "manifest.json").read_text()
    assert (output / "proposal.md").read_text() == "# Proposed change\n"
    assert not (output / "prompt.json").exists()


def test_over_budget_never_generates(http, tmp_path, monkeypatch):
    calls, responses = http
    monkeypatch.setenv("ANTHROPIC_API_TOKEN", KEY)
    responses[helper.MESSAGES + "/count_tokens"]["input_tokens"] = 3_000_000
    code, output, manifest = invoke(tmp_path, "--execute")
    assert code == 1
    assert calls[-1].full_url.endswith("/count_tokens")
    assert manifest["reserved_cost_microusd"] > manifest["budget_microusd"] == 5_000_000
    assert manifest["generation_attempted"] is False
    assert not (output / "proposal.md").exists()


def test_failed_generation_never_retries_or_exposes_server_body(
    http,
    tmp_path,
    monkeypatch,
    capsys,
):
    calls, responses = http
    monkeypatch.setenv("ANTHROPIC_API_TOKEN", KEY)
    responses[helper.MESSAGES] = HTTPError(helper.MESSAGES, 503, KEY, {}, io.BytesIO(KEY.encode()))
    code, output, manifest = invoke(tmp_path, "--execute")
    assert code == 1
    assert [request.full_url for request in calls].count(helper.MESSAGES) == 1
    assert manifest["generation_attempted"] is True
    assert manifest["status"] == "failed"
    assert KEY not in capsys.readouterr().err
    assert not (output / "proposal.md").exists()


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        "docs/../.env",
        "/docs/api.md",
        "tests/credentials.py",
        "docs/$(touch bad).md",
        "research/paper/notes.md",
        "docs/.private/readme.md",
        ".github/workflows/ci.yml",
    ],
)
def test_rejects_disallowed_paths_before_network(http, tmp_path, path):
    calls, _ = http
    assert invoke(tmp_path, f"--files={path}")[0] == 1
    assert calls == []


@pytest.mark.parametrize("mode", ["120000", "160000", "040000"])
def test_rejects_links_and_nonregular_objects(http, tmp_path, mode):
    calls, responses = http
    responses[f"{helper.GITHUB}/git/trees/{TREE}?recursive=1"]["tree"][0]["mode"] = mode
    assert invoke(tmp_path, f"--files={FILE}")[0] == 1
    assert not any("/git/blobs/" in request.full_url for request in calls)


def test_stale_commit_and_pull_requests_are_rejected(http, tmp_path):
    calls, responses = http
    assert invoke(tmp_path, "--commit=" + "f" * 40)[0] == 1
    assert all("/git/commits/" not in request.full_url for request in calls)
    responses[f"{helper.GITHUB}/issues/71"]["pull_request"] = {}
    assert invoke(tmp_path)[0] == 1
    assert calls[-1].full_url == f"{helper.GITHUB}/issues/71"


def test_missing_key_does_not_contact_claude(http, tmp_path):
    calls, _ = http
    code, _, manifest = invoke(tmp_path, "--execute")
    assert code == 1
    assert manifest["generation_attempted"] is False
    assert all(request.full_url.startswith(helper.GITHUB) for request in calls)


def test_preview_rejects_credential_shapes_in_public_issue(http, tmp_path):
    calls, responses = http
    responses[f"{helper.GITHUB}/issues/71"]["body"] = "-----BEGIN PRIVATE KEY-----"
    code, output, _ = invoke(tmp_path)
    assert code == 1
    assert not output.exists()
    assert all(request.full_url.startswith(helper.GITHUB) for request in calls)
