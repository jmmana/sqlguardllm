#!/usr/bin/env python3
"""Stage the standalone engines for the NuGet package.

    python clients/dotnet/build/stage-engines.py --engines engines            # all five platforms
    python clients/dotnet/build/stage-engines.py --engines engines --platform macos-arm64
    python clients/dotnet/build/stage-engines.py --check-nupkg clients/dotnet/out/SqlGuardLlm.0.5.0-alpha.5.nupkg

--engines is the folder scripts/fetch-engine.py unpacked (``<platform>/<archive folder>/sqlguardllm-engine``),
or any folder with that shape. Each engine is copied (symbolic links resolved) to
``clients/dotnet/src/SqlGuardLlm/engines/<rid>/sqlguardllm-engine/`` next to an ``engine.sha256`` that lists the SHA-256
of every file; SqlGuardLlm.csproj packs that folder to ``runtimes/<rid>/native/sqlguardllm-engine/`` and the client
recomputes every hash before it starts the engine. ``--check-nupkg`` fails when a package passes 150 MB (the point at
which the engines must be split into SqlGuardLlm.Engine.<rid> packages) or lacks one of the five engines.
Standard library only. Exit 1 on any problem.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import zipfile
from pathlib import Path

RIDS = {"linux-x64": "linux-x64", "linux-arm64": "linux-arm64", "macos-x64": "osx-x64", "macos-arm64": "osx-arm64", "windows-x64": "win-x64"}
FOLDER = "sqlguardllm-engine"
MANIFEST = "engine.sha256"
LIMIT = 150 * 1024 * 1024
DEST = Path(__file__).resolve().parent.parent / "src" / "SqlGuardLlm" / "engines"


def find_engine_folder(root: Path, rid: str) -> Path:
    exe = FOLDER + (".exe" if rid.startswith("win-") else "")
    found = sorted(p.parent for p in root.rglob(exe) if p.is_file())
    if len(found) != 1:
        raise SystemExit(f"expected one {exe} under {root}, found {len(found)}")
    return found[0]


def write_manifest(folder: Path) -> int:
    lines = []
    for path in sorted(folder.rglob("*")):
        if path.is_file() and path.name != MANIFEST:
            lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(folder).as_posix()}")
    (folder / MANIFEST).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return len(lines)


def stage(engines: Path, platforms: list[str]) -> None:
    shutil.rmtree(DEST, ignore_errors=True)
    for plat in platforms:
        rid = RIDS[plat]
        source = find_engine_folder(engines / plat, rid)
        target = DEST / rid / FOLDER
        shutil.copytree(source, target, symlinks=False)
        print(f"{rid}: {write_manifest(target)} files from {source}")


def check_nupkg(path: Path, partial: bool = False) -> None:
    size = path.stat().st_size
    print(f"{path.name}: {size / 1024 / 1024:.1f} MiB (limit {LIMIT // 1024 // 1024} MiB)")
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
    missing = [r for r in RIDS.values() if f"runtimes/{r}/native/{FOLDER}/{MANIFEST}" not in names]
    if missing and not partial:
        raise SystemExit(f"the package carries no engine for: {', '.join(missing)}")
    if size > LIMIT:
        raise SystemExit("the package is over 150 MB: split the engines into SqlGuardLlm.Engine.<rid> packages")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--engines", type=Path, help="folder unpacked by scripts/fetch-engine.py")
    p.add_argument("--platform", action="append", choices=sorted(RIDS), help="only these (default: all five)")
    p.add_argument("--check-nupkg", type=Path, help="check a packed .nupkg instead of staging")
    p.add_argument("--allow-partial", action="store_true", help="with --check-nupkg: do not require all five engines")
    a = p.parse_args()
    if a.check_nupkg:
        check_nupkg(a.check_nupkg, a.allow_partial)
    elif a.engines:
        stage(a.engines, a.platform or list(RIDS))
    else:
        p.error("give --engines or --check-nupkg")


if __name__ == "__main__":
    sys.exit(main())
