"""research/redteam/run.py with a fake Anthropic client: no network, no key, no spend."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).parents[1]
_spec = importlib.util.spec_from_file_location("redteam_run", ROOT / "research/redteam/run.py")
assert _spec and _spec.loader
run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run)

from fuzz_engines import Backend, SQLiteBackend  # noqa: E402

FIXTURE = ROOT / "tests" / "data" / "redteam" / "2026-01-01"
KEY = "sk-ant-test-key-for-stub-only-000000"
OK_SQL = "SELECT id, branch_id, total FROM invoices ORDER BY id"


def attack(sql: str = OK_SQL, technique: str = "t", dialect: str = "sqlite") -> dict[str, str]:
    return {
        "sql": sql,
        "dialect": dialect,
        "technique": technique,
        "target_invariant": "foreign_rows",
        "rationale": "r",
    }


class FakeStream:
    def __init__(self, response: Any) -> None:
        self.response = response

    def __enter__(self) -> FakeStream:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def get_final_message(self) -> Any:
        return self.response


class FakeClient:
    def __init__(
        self,
        text: str = "",
        *,
        stop_reason: str = "end_turn",
        counted: int = 9_000,
        usage: tuple[int, int] = (9_000, 4_000),
        blocks: list[Any] | None = None,
    ) -> None:
        content = blocks if blocks is not None else [SimpleNamespace(type="text", text=text)]
        self.response = SimpleNamespace(
            model="claude-opus-5-5-served",
            stop_reason=stop_reason,
            stop_details=SimpleNamespace(category="cyber") if stop_reason == "refusal" else None,
            content=content,
            usage=SimpleNamespace(input_tokens=usage[0], output_tokens=usage[1]),
        )
        self.counted = counted
        self.requests: list[dict[str, Any]] = []
        self.counts: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(count_tokens=self._count)
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _count(self, **kwargs: Any) -> Any:
        self.counts.append(kwargs)
        return SimpleNamespace(input_tokens=self.counted)

    def _stream(self, **kwargs: Any) -> FakeStream:
        self.requests.append(kwargs)
        return FakeStream(self.response)


def payload(*attacks: dict[str, str]) -> str:
    return json.dumps({"attacks": list(attacks)})


@pytest.fixture(autouse=True)
def only_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    monkeypatch.setattr(run, "open_backends", lambda names=None: {"sqlite": SQLiteBackend()})


def go(tmp_path: Path, client: FakeClient, *extra: str) -> tuple[int, dict[str, Any], list[Any]]:
    code = run.main(["--output-dir", str(tmp_path), "--date", "d", *extra], client=client)
    out = tmp_path / "d"
    manifest = json.loads((out / "manifest.json").read_text())
    path = out / "attacks.jsonl"
    rows = [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []
    return code, manifest, rows


def test_prompt_states_the_defensive_purpose_and_sha_is_recorded() -> None:
    text = run.system_prompt()
    first = text.split("\n\n")[0].lower()
    assert "defensive" in first and "sqlguardllm" in first and "synthetic" in first
    assert "disposable test databases" in first
    recorded = (ROOT / "research/redteam/prompt.sha256").read_text().strip()
    assert recorded == hashlib.sha256(text.encode()).hexdigest(), "bump the prompt version"


def test_request_shape_and_key_never_in_the_prompt(tmp_path: Path) -> None:
    client = FakeClient(payload(attack()))
    code, manifest, _ = go(tmp_path, client)
    assert code == 0
    (request,) = client.requests
    assert request["model"] == "claude-opus-5-5"
    assert request["thinking"] == {"type": "adaptive"}
    assert request["output_config"]["format"]["type"] == "json_schema"
    assert request["betas"] == ["server-side-fallback-2026-07-01"]
    assert request["fallbacks"] == "default"
    assert request["max_tokens"] == run.DEFAULT_TOKEN_BUDGET
    assert request["system"] == run.system_prompt()
    assert KEY not in json.dumps(request) and KEY not in json.dumps(client.counts)
    assert manifest["prompt_sha256"] == hashlib.sha256(run.system_prompt().encode()).hexdigest()
    assert "ATTACK_GALLERY" in request["messages"][0]["content"]


def test_budget_refusal_sends_nothing(tmp_path: Path) -> None:
    client = FakeClient(payload(attack()), counted=5_000_000)
    code, manifest, _ = go(tmp_path, client, "--max-usd", "0.01")
    assert code == 1
    assert client.requests == []
    assert manifest["status"] == "refused_by_budget"


def test_default_estimate_is_under_the_default_ceiling(tmp_path: Path) -> None:
    client = FakeClient(payload(attack()))
    _, manifest, _ = go(tmp_path, client)
    assert manifest["estimate"]["estimated_cost_microusd"] < round(run.DEFAULT_MAX_USD * 1e6)


def test_caps_are_enforced() -> None:
    for bad in (["--max-attacks", "51"], ["--max-usd", "5.01"], ["--token-budget", "10"]):
        with pytest.raises(SystemExit):
            run.main(bad, client=FakeClient())


def test_preflight_only_counts_but_does_not_generate(tmp_path: Path) -> None:
    client = FakeClient(payload(attack()))
    code, manifest, _ = go(tmp_path, client, "--preflight-only")
    assert code == 0 and client.requests == [] and len(client.counts) == 1
    assert manifest["status"] == "preflight"


def test_refusal_is_recorded_and_is_not_a_failure(tmp_path: Path) -> None:
    client = FakeClient(stop_reason="refusal", blocks=[], usage=(9_000, 10))
    code, manifest, rows = go(tmp_path, client)
    assert code == 0
    assert manifest["status"] == "refused"
    assert manifest["refusal_category"] == "cyber"
    assert rows == [] and manifest["generated"] == 0


def test_thinking_and_fallback_blocks_are_handled(tmp_path: Path) -> None:
    blocks = [
        SimpleNamespace(type="thinking", thinking=""),
        SimpleNamespace(type="fallback"),
        SimpleNamespace(type="text", text=payload(attack())),
    ]
    _, manifest, rows = go(tmp_path, FakeClient(blocks=blocks))
    assert manifest["fallback_used"] is True and len(rows) == 1


def test_parse_attacks_and_malformed_output(tmp_path: Path) -> None:
    good = attack()
    bad_dialect = {**attack(), "dialect": "oracle"}
    missing = {"sql": "SELECT 1"}
    parsed, bad = run.parse_attacks(payload(good, bad_dialect, missing))
    assert parsed == [good] and bad == 2
    assert run.parse_attacks("not json") == ([], 0)
    assert run.parse_attacks('{"attacks": 3}') == ([], 0)
    assert run.parse_attacks("[1]") == ([], 0)

    code, manifest, rows = go(tmp_path, FakeClient("```json garbage"))
    assert code == 1 and manifest["status"] == "malformed" and rows == []

    code, manifest, rows = go(tmp_path, FakeClient(payload(good, missing)))
    assert code == 0 and manifest["malformed_items"] == 1 and len(rows) == 1


def test_duplicates_are_fingerprinted_counted_and_kept(tmp_path: Path) -> None:
    same_a = attack("SELECT id FROM invoices WHERE id > 3", "Time Based")
    same_b = attack("select id  from invoices where id > 99 /* c */", "time based")
    other = attack("SELECT name FROM branches", "other")
    _, manifest, rows = go(tmp_path, FakeClient(payload(same_a, same_b, other)))
    assert len(rows) == 3
    assert rows[0]["fingerprint"] == rows[1]["fingerprint"] != rows[2]["fingerprint"]
    assert [r["duplicate_of"] for r in rows] == [None, 0, None]
    assert manifest["generated"] == 3 and manifest["unique"] == 2 and manifest["duplicates"] == 1
    assert manifest["by_technique"] == {"time based": 2, "other": 1}


def test_records_and_manifest_carry_the_provenance_and_cost(tmp_path: Path) -> None:
    client = FakeClient(
        payload(attack(), attack("SELECT email FROM customers", dialect="postgres"))
    )
    code, manifest, rows = go(tmp_path, client)
    assert code == 0
    first, second = rows
    assert first["model"] == "claude-opus-5-5-served" == manifest["model_reported"]
    for key in ("prompt_sha256", "policy_sha256", "sqlguardllm_version", "git_sha", "sql",
                "technique", "verdict", "engines", "fix_release"):  # fmt: skip
        assert key in first
    assert first["verdict"]["allowed"] is True and "branch_id" in first["verdict"]["rewritten_sql"]
    assert first["engines"]["sqlite"]["north"]["outcome"] == "executed"
    assert first["engines"]["duckdb"] == {"outcome": "not_applicable"}
    assert second["verdict"]["allowed"] is False
    assert second["verdict"]["violations"][0]["code"] == "hidden_column"
    assert second["status"] == "rejected"
    assert manifest["usage"] == {"input_tokens": 9_000, "output_tokens": 4_000}
    assert manifest["cost_microusd"] == 9_000 * 4 + 4_000 * 20 == 116_000
    assert manifest["cost_usd"] == 0.116
    assert manifest["allowed"] == 1 and manifest["rejected"] == 1 and manifest["bypasses"] == []


def test_cost_math() -> None:
    assert run.cost_microusd(1_000_000, 0) == 4_000_000
    assert run.cost_microusd(0, 1_000_000) == 20_000_000
    est = run.estimate(10_000, 20_000, 32_000)
    assert est["reserved_input_tokens"] == 24_096  # the byte reserve beats the 25% reserve
    assert est["estimated_cost_microusd"] == 24_096 * 4 + 32_000 * 20
    assert run.estimate(100_000, 10, 1_000)["reserved_input_tokens"] == 125_000


class LeakyBackend(Backend):
    """An engine whose every query returns a row of branch 4, which no role may see."""

    name = dialect = "sqlite"

    def query(self, world: str, sql: str) -> list[tuple[Any, ...]]:
        return [(1, "CANARY_T4_leak")]

    def close(self) -> None:
        return None


def test_a_canary_in_the_output_is_reported_as_a_bypass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(run, "open_backends", lambda names=None: {"sqlite": LeakyBackend()})
    client = FakeClient(payload(attack("SELECT id FROM branches", "leaky")))
    code, manifest, rows = go(tmp_path, client)
    assert code == 1
    assert rows[0]["status"] == "bypass"
    assert manifest["bypasses"][0]["technique"] == "leaky"
    assert "BYPASSES FOUND: 1" in capsys.readouterr().out
    cases = yaml.safe_load((tmp_path / "d" / "redteam_cases.yaml").read_text())
    assert cases[0]["expect"] == "reject" and cases[0]["pending"]
    assert cases[0]["id"].startswith("redteam-sqlite-")


def test_rescore_a_stored_dataset_without_the_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    copy = tmp_path / "2026-01-01"
    shutil.copytree(FIXTURE, copy)
    assert run.main(["--rescore", str(copy)], client=None) == 0
    summary = json.loads((copy / "rescore.json").read_text())
    assert summary["bypasses"] == [] and summary["changed_verdicts"] == []
    assert summary["totals"]["generated"] == 4 and summary["totals"]["duplicates"] == 1
    assert summary["totals"]["rejected"] == 1


def test_rescore_reports_a_changed_verdict(tmp_path: Path) -> None:
    copy = tmp_path / "x"
    shutil.copytree(FIXTURE, copy)
    lines = [json.loads(x) for x in (copy / "attacks.jsonl").read_text().splitlines()]
    lines[1]["verdict"]["allowed"] = True  # the guard "used to" allow it
    (copy / "attacks.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    summary = run.rescore(copy, {"sqlite": None})
    assert summary["changed_verdicts"] == [{"index": 1, "was_allowed": True, "now_allowed": False}]


def test_workflow_runs_only_on_manual_dispatch() -> None:
    text = (ROOT / ".github/workflows/redteam.yml").read_text()
    workflow = yaml.safe_load(text)
    triggers = workflow.get("on") or workflow[True]
    assert set(triggers) == {"workflow_dispatch"}  # no schedule: the owner caps the spend
    assert workflow["permissions"] == {"contents": "read"}
    assert "pull_request" not in text and "git push" not in text
    assert text.count("ANTHROPIC_API_TOKEN") == 1
    assert "ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_TOKEN }}" in text
