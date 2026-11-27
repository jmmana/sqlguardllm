#!/usr/bin/env python3
"""Download and verify the standalone engine binaries of a sqlguardllm release.

    python scripts/fetch-engine.py 0.5.0 [--out engines] [--platform linux-x64 ...]

For VERSION it downloads ``SHA256SUMS`` and the engine archives of the release ``vVERSION`` of
jmmana/sqlguardllm, then fails (exit 1) on anything wrong: an asset missing from the release or
from SHA256SUMS, a checksum mismatch, a failed ``gh attestation verify`` (run when ``gh`` is
installed; ``--require-attestation`` makes a missing ``gh`` an error too), an unsafe archive
member, or, for the platform of this machine, an engine whose ``hello`` does not report VERSION
and protocol 1. Each archive is unpacked to ``<out>/<platform>/`` only after it verified.

Standard library only; used by the Node, .NET and Java packaging jobs. Asset names and the
verification steps are in docs/PROTOCOL.md. Exit codes: 0 verified, 1 verification failed,
2 usage error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

REPO = "jmmana/sqlguardllm"
PROTOCOL_VERSION = 1
PLATFORMS = {
    "linux-x64": "sqlguardllm-engine-linux-x64.tar.gz",
    "linux-arm64": "sqlguardllm-engine-linux-arm64.tar.gz",
    "macos-arm64": "sqlguardllm-engine-macos-arm64.tar.gz",
    "macos-x64": "sqlguardllm-engine-macos-x64.tar.gz",
    "windows-x64": "sqlguardllm-engine-windows-x64.zip",
}
SUMS = "SHA256SUMS"
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:(?:a|b|rc)[0-9]+)?$")
SUM_LINE = re.compile(r"^([0-9a-f]{64}) [ *]([^/\\\s][^/\\]*)$")


class Failure(Exception):
    """Something did not verify. The message says what; nothing is unpacked or kept."""


def host_platform() -> str | None:
    system = {"darwin": "macos", "linux": "linux", "win32": "windows"}.get(sys.platform)
    machine = platform.machine().lower()
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64", "amd64": "x64"}.get(machine)
    name = f"{system}-{arch}"
    return name if name in PLATFORMS else None


def fetch(base: str, name: str, target: Path) -> None:
    """Copy ``name`` from a local directory or download it over https."""
    if "://" not in base:
        source = Path(base) / name
        if not source.is_file():
            raise Failure(f"{name} is not in {base}")
        shutil.copyfile(source, target)
        return
    if urllib.parse.urlsplit(base).scheme != "https":
        raise Failure("downloads must use https")
    url = f"{base.rstrip('/')}/{name}"
    try:
        with urllib.request.urlopen(url, timeout=120) as response, target.open("wb") as out:
            shutil.copyfileobj(response, out)
    except OSError as exc:
        raise Failure(f"cannot download {url}: {exc}") from exc


def parse_sums(text: str) -> dict[str, str]:
    """``<sha256>  <file name>`` per line, as written by ``sha256sum``."""
    sums: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        match = SUM_LINE.match(line)
        if not match:
            raise Failure(f"{SUMS} has a malformed line: {line[:80]!r}")
        if match.group(2) in sums:
            raise Failure(f"{SUMS} lists {match.group(2)} twice")
        sums[match.group(2)] = match.group(1)
    return sums


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_attestation(path: Path, repo: str, required: bool) -> str:
    gh = shutil.which("gh")
    if gh is None:
        if required:
            raise Failure("gh is not installed and --require-attestation was given")
        return "skipped (gh not installed)"
    result = subprocess.run(
        [gh, "attestation", "verify", str(path), "--repo", repo],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()[-1:] or [""]
        raise Failure(f"gh attestation verify failed for {path.name}: {detail[0]}")
    return "verified"


def unpack(archive: Path, target: Path) -> None:
    """Unpack into ``target`` after checking every member stays inside it."""
    root = target.resolve()

    def check(name: str) -> None:
        pure = PurePosixPath(name.replace("\\", "/"))
        if pure.is_absolute() or ".." in pure.parts or re.match(r"^[A-Za-z]:", name):
            raise Failure(f"unsafe path in {archive.name}: {name!r}")
        if not (root / pure).resolve().is_relative_to(root):
            raise Failure(f"unsafe path in {archive.name}: {name!r}")

    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                check(member.filename)
            bundle.extractall(target)
        return
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        for member in members:
            check(member.name)
            if member.isdev() or member.isfifo():
                raise Failure(f"special file in {archive.name}: {member.name!r}")
            if member.issym() or member.islnk():
                link = PurePosixPath(member.linkname)
                if member.issym():
                    link = PurePosixPath(member.name).parent / link
                check(str(link))
        if hasattr(tarfile, "data_filter"):  # Python 3.12+ and the security backports
            tar.extractall(target, members=members, filter="data")
        else:
            tar.extractall(target, members=members)


def find_engine(folder: Path) -> Path:
    for candidate in folder.glob("*/sqlguardllm-engine*"):
        if candidate.is_file() and candidate.suffix in ("", ".exe"):
            return candidate
    raise Failure(f"no sqlguardllm-engine executable under {folder}")


def check_hello(engine: Path, version: str) -> None:
    """Lockstep: the engine must report exactly the requested version and protocol 1."""
    try:
        proc = subprocess.run(
            [str(engine), "--stdio"],
            input='{"id": 1, "type": "hello"}\n',
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        hello = json.loads(proc.stdout.splitlines()[0])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as exc:
        raise Failure(f"{engine.name} did not answer hello: {exc}") from exc
    if hello.get("protocol") != PROTOCOL_VERSION or hello.get("engine_version") != version:
        raise Failure(
            f"{engine.name} reports engine {hello.get('engine_version')!r} protocol "
            f"{hello.get('protocol')!r}, expected {version!r} protocol {PROTOCOL_VERSION}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("version", help="release version without the v, e.g. 0.5.0 or 0.5.0a3")
    parser.add_argument("--out", type=Path, default=Path("engines"))
    parser.add_argument("--platform", action="append", choices=sorted(PLATFORMS), dest="platforms")
    parser.add_argument("--repo", default=REPO, help=f"default {REPO}")
    parser.add_argument(
        "--base-url",
        help="an https URL or a local directory holding the assets (default: the GitHub release)",
    )
    parser.add_argument("--require-attestation", action="store_true")
    parser.add_argument(
        "--no-attestation",
        action="store_true",
        help="skip gh attestation verify (a local --base-url never has attestations to check)",
    )
    parser.add_argument(
        "--no-extract", action="store_true", help="verify and keep the archives only"
    )
    args = parser.parse_args(argv)
    if not VERSION_RE.match(args.version):
        print(
            f"error: {args.version!r} is not a release version like 0.5.0 or 0.5.0a3",
            file=sys.stderr,
        )
        return 2
    base = args.base_url or f"https://github.com/{args.repo}/releases/download/v{args.version}"
    wanted = args.platforms or list(PLATFORMS)
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    attest = not args.no_attestation and "://" in base
    host = host_platform()
    try:
        sums_path = out / SUMS
        fetch(base, SUMS, sums_path)
        sums = parse_sums(sums_path.read_text(encoding="utf-8"))
        if attest:
            note = verify_attestation(sums_path, args.repo, args.require_attestation)
            print(f"{SUMS}: attestation {note}")
        for name in wanted:
            asset = PLATFORMS[name]
            if asset not in sums:
                raise Failure(f"{asset} is not listed in {SUMS}")
            archive = out / asset
            fetch(base, asset, archive)
            actual = sha256(archive)
            if actual != sums[asset]:
                archive.unlink(missing_ok=True)
                raise Failure(
                    f"checksum mismatch for {asset}: expected {sums[asset]}, got {actual}"
                )
            note = (
                verify_attestation(archive, args.repo, args.require_attestation)
                if attest
                else "not checked"
            )
            print(f"{asset}: sha256 ok, attestation {note}")
            if args.no_extract:
                continue
            folder = out / name
            unpack(archive, folder)
            if name == host:
                check_hello(find_engine(folder), args.version)
                print(f"{asset}: hello ok (engine {args.version}, protocol {PROTOCOL_VERSION})")
    except Failure as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
