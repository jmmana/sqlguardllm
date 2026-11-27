#!/usr/bin/env python3
"""Build the standalone engine binary of this platform with PyInstaller.

    uv run --isolated --no-dev --with pyinstaller==6.22.3 \\
        python scripts/build-engine.py [--out dist-engine]

Default (``--mode onedir``, the release format): ``<out>/sqlguardllm-engine-<os>-<arch>/`` holding
``sqlguardllm-engine[.exe]`` and its ``_internal`` folder, and the release asset
``<out>/sqlguardllm-engine-<os>-<arch>.tar.gz`` (``.zip`` on Windows) with that folder in it.
``--mode onefile`` writes one self-extracting executable instead, for benchmarking only: it unpacks
itself on every start, which costs seconds on macOS (benchmarks/engine-binaries.json).

The binary contains sqlguardllm, sqlglot and the standard library only: optional extras
(yaml, sqlalchemy, mcp, opentelemetry) and the interpreter's GUI, test and several network-client
modules are excluded. See docs/PROTOCOL.md for the asset names and how to verify them.
"""

from __future__ import annotations

import argparse
import platform
import shutil
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDES = [
    "yaml", "sqlalchemy", "mcp", "opentelemetry", "tkinter", "unittest", "pydoc", "doctest",
    "test", "distutils", "setuptools", "pip", "ssl", "_ssl", "smtplib", "ftplib", "imaplib",
    "poplib", "telnetlib", "xmlrpc", "curses", "sqlite3", "lib2to3", "asyncio", "multiprocessing",
]  # fmt: skip


def platform_name() -> str:
    system = {"darwin": "macos", "linux": "linux", "win32": "windows"}[sys.platform]
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        arch = "arm64"
    elif machine in ("x86_64", "amd64"):
        arch = "x64"
    else:
        arch = machine
    return f"{system}-{arch}"


def archive_suffix() -> str:
    return ".zip" if sys.platform == "win32" else ".tar.gz"


def archive(folder: Path) -> Path:
    """Pack the one-dir build into the release asset (symlinks kept; fixed timestamps)."""
    if sys.platform == "win32":
        target = folder.parent / (folder.name + ".zip")
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted(folder.rglob("*")):
                if path.is_file():
                    info = zipfile.ZipInfo.from_file(path, path.relative_to(folder.parent))
                    info.date_time = (2020, 1, 1, 0, 0, 0)
                    info.compress_type = zipfile.ZIP_DEFLATED
                    bundle.writestr(info, path.read_bytes())
        return target
    target = folder.parent / (folder.name + ".tar.gz")

    def fix(info: tarfile.TarInfo) -> tarfile.TarInfo:
        info.mtime, info.uid, info.gid, info.uname, info.gname = 0, 0, 0, "", ""
        return info

    with tarfile.open(target, "w:gz", compresslevel=9) as bundle:
        bundle.add(folder, arcname=folder.name, filter=fix)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--mode", choices=["onefile", "onedir"], default="onedir")
    parser.add_argument("--out", default="dist-engine")
    parser.add_argument("--strip", action="store_true", help="strip symbols (not on Windows)")
    args = parser.parse_args()
    import PyInstaller.__main__ as pyinstaller  # noqa: PLC0415

    out = Path(args.out).resolve()
    work = out / "_build"
    plat = f"sqlguardllm-engine-{platform_name()}"
    argv = [
        str(ROOT / "scripts" / "engine_main.py"),
        "--name", "sqlguardllm-engine",
        f"--{args.mode}", "--noconfirm", "--clean", "--console",
        "--distpath", str(work / "dist"), "--workpath", str(work), "--specpath", str(work),
        "--paths", str(ROOT / "src"),
        "--collect-submodules", "sqlglot.dialects",
        "--collect-submodules", "sqlguardllm",
    ]  # fmt: skip
    for module in EXCLUDES:
        argv += ["--exclude-module", module]
    if args.strip and sys.platform != "win32":
        argv.append("--strip")
    pyinstaller.run(argv)
    built = work / "dist" / "sqlguardllm-engine"
    if args.mode == "onefile":
        target = out / (plat + (".exe" if sys.platform == "win32" else ""))
        shutil.move(str(built.with_suffix(".exe") if sys.platform == "win32" else built), target)
    else:
        target = out / plat
        shutil.rmtree(target, ignore_errors=True)
        shutil.move(str(built), target)
        print(archive(target))
    shutil.rmtree(work, ignore_errors=True)
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
