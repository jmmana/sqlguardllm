"""scripts/fetch-engine.py against a fake release directory: it must fail on anything unverified."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import sys
import tarfile
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fetch-engine.py"
spec = importlib.util.spec_from_file_location("fetch_engine", SCRIPT)
assert spec and spec.loader
fetch_engine: ModuleType = importlib.util.module_from_spec(spec)
sys.modules["fetch_engine"] = fetch_engine
spec.loader.exec_module(fetch_engine)

VERSION = "0.5.0a3"
FAKE_ENGINE = (
    "#!/bin/sh\n"
    "read line\n"
    f'echo \'{{"id": 1, "type": "hello", "protocol": 1, "engine_version": "{VERSION}"}}\'\n'
)
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="the fake engine is a shell script")


def make_release(
    directory: Path, *, engine: str = FAKE_ENGINE, members: dict[str, str] | None = None
) -> dict[str, str]:
    """A release directory with the five archives and SHA256SUMS. Returns name -> sha256."""
    sums: dict[str, str] = {}
    for platform_name, asset in fetch_engine.PLATFORMS.items():
        path = directory / asset
        files = members or {f"sqlguardllm-engine-{platform_name}/sqlguardllm-engine": engine}
        if asset.endswith(".zip"):
            with zipfile.ZipFile(path, "w") as bundle:
                for name, content in files.items():
                    bundle.writestr(name, content)
        else:
            with tarfile.open(path, "w:gz") as tar:
                for name, content in files.items():
                    data = content.encode()
                    info = tarfile.TarInfo(name)
                    info.size, info.mode = len(data), 0o755
                    tar.addfile(info, io.BytesIO(data))
        sums[asset] = hashlib.sha256(path.read_bytes()).hexdigest()
    write_sums(directory, sums)
    return sums


def write_sums(directory: Path, sums: dict[str, str]) -> None:
    (directory / "SHA256SUMS").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in sums.items()), encoding="utf-8"
    )


def run(release: Path, out: Path, *extra: str) -> int:
    return int(
        fetch_engine.main(
            [VERSION, "--base-url", str(release), "--out", str(out), "--no-attestation", *extra]
        )
    )


def test_matching_checksums_pass(tmp_path: Path) -> None:
    (tmp_path / "rel").mkdir()
    make_release(tmp_path / "rel")
    assert run(tmp_path / "rel", tmp_path / "out", "--no-extract") == 0
    assert (tmp_path / "out" / "sqlguardllm-engine-linux-x64.tar.gz").is_file()


@posix_only
def test_unpacks_and_runs_hello_for_the_host(tmp_path: Path) -> None:
    (tmp_path / "rel").mkdir()
    make_release(tmp_path / "rel")
    host = fetch_engine.host_platform()
    assert host is not None
    assert run(tmp_path / "rel", tmp_path / "out", "--platform", host) == 0
    assert (tmp_path / "out" / host / f"sqlguardllm-engine-{host}" / "sqlguardllm-engine").is_file()


@posix_only
def test_engine_of_another_version_fails(tmp_path: Path) -> None:
    (tmp_path / "rel").mkdir()
    make_release(tmp_path / "rel", engine=FAKE_ENGINE.replace(VERSION, "0.6.0"))
    host = fetch_engine.host_platform()
    assert host is not None
    assert run(tmp_path / "rel", tmp_path / "out", "--platform", host) == 1


def test_checksum_mismatch_fails_and_keeps_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    release = tmp_path / "rel"
    release.mkdir()
    sums = make_release(release)
    sums["sqlguardllm-engine-macos-arm64.tar.gz"] = "0" * 64
    write_sums(release, sums)
    assert run(release, tmp_path / "out") == 1
    assert "checksum mismatch" in capsys.readouterr().err
    assert not (tmp_path / "out" / "sqlguardllm-engine-macos-arm64.tar.gz").exists()
    assert not (tmp_path / "out" / "macos-arm64").exists()


def test_tampered_asset_fails(tmp_path: Path) -> None:
    release = tmp_path / "rel"
    release.mkdir()
    make_release(release)
    with (release / "sqlguardllm-engine-linux-arm64.tar.gz").open("ab") as handle:
        handle.write(b"x")
    assert run(release, tmp_path / "out", "--no-extract") == 1


def test_missing_asset_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    release = tmp_path / "rel"
    release.mkdir()
    make_release(release)
    (release / "sqlguardllm-engine-windows-x64.zip").unlink()
    assert run(release, tmp_path / "out", "--no-extract") == 1
    assert "windows-x64.zip is not in" in capsys.readouterr().err


def test_asset_missing_from_sums_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    release = tmp_path / "rel"
    release.mkdir()
    sums = make_release(release)
    del sums["sqlguardllm-engine-linux-x64.tar.gz"]
    write_sums(release, sums)
    assert run(release, tmp_path / "out", "--no-extract") == 1
    assert "not listed in SHA256SUMS" in capsys.readouterr().err


def test_missing_sums_file_fails(tmp_path: Path) -> None:
    release = tmp_path / "rel"
    release.mkdir()
    make_release(release)
    (release / "SHA256SUMS").unlink()
    assert run(release, tmp_path / "out", "--no-extract") == 1


def test_malformed_sums_fails(tmp_path: Path) -> None:
    release = tmp_path / "rel"
    release.mkdir()
    make_release(release)
    (release / "SHA256SUMS").write_text("not a checksum line\n", encoding="utf-8")
    assert run(release, tmp_path / "out", "--no-extract") == 1


def test_path_traversal_in_archive_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    release = tmp_path / "rel"
    release.mkdir()
    make_release(release, members={"../evil": "x"})
    assert run(release, tmp_path / "out", "--platform", "linux-x64") == 1
    assert "unsafe path" in capsys.readouterr().err
    assert not (tmp_path / "evil").exists()


def test_bad_version_is_a_usage_error(tmp_path: Path) -> None:
    assert fetch_engine.main(["latest", "--out", str(tmp_path)]) == 2


def test_https_is_required_for_remote_downloads(tmp_path: Path) -> None:
    code = fetch_engine.main(
        [
            VERSION,
            "--base-url",
            "http://example.invalid/x",
            "--out",
            str(tmp_path),
            "--no-attestation",
        ]
    )
    assert code == 1
