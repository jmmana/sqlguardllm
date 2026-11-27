#!/usr/bin/env python3
"""Measure an engine binary: size, cold start (spawn to first `hello` response) and peak memory.

    python scripts/bench-engine.py BINARY_OR_DIR [--label onefile] [--runs 10] [--json out.json]

Standard library only. BINARY_OR_DIR is the executable, or a one-dir folder holding it. Peak
memory is the largest resident set of the process tree as reported by the OS (ru_maxrss of the
waited children); it is null on Windows. Cold start is measured on a fresh process every time:
a one-file binary unpacks itself on each run, so every run pays that cost.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path


def find_executable(path: Path) -> Path:
    if path.is_file():
        return path
    for candidate in (path / "sqlguardllm-engine", path / "sqlguardllm-engine.exe"):
        if candidate.is_file():
            return candidate
    raise SystemExit(f"no executable in {path}")


def folder_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def cold_start(exe: Path) -> float:
    start = time.perf_counter()
    proc = subprocess.Popen(
        [str(exe), "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(b'{"id": 1, "type": "hello"}\n')
    proc.stdin.flush()
    line = proc.stdout.readline()
    elapsed = time.perf_counter() - start
    if json.loads(line).get("protocol") != 1:
        raise SystemExit(f"unexpected hello response: {line!r}")
    proc.stdin.close()
    proc.wait(timeout=30)
    proc.stdout.close()
    return elapsed


def peak_rss_mb(exe: Path) -> float | None:
    if sys.platform == "win32":
        return None
    import resource  # noqa: PLC0415

    code = (
        "import resource, subprocess, sys\n"
        "p = subprocess.Popen([sys.argv[1], '--stdio'], stdin=subprocess.PIPE,"
        " stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)\n"
        'p.stdin.write(b\'{"id":1,"type":"hello"}\\n\'); p.stdin.flush(); p.stdout.readline()\n'
        "p.stdin.close(); p.wait()\n"
        "print(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code, str(exe)], capture_output=True, text=True, check=True
    ).stdout
    raw = int(out.strip())
    del resource
    return round(raw / (1024 * 1024 if sys.platform == "darwin" else 1024), 1)


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("target", type=Path)
    parser.add_argument("--label", default="onefile")
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    exe = find_executable(args.target)
    times = [cold_start(exe) for _ in range(args.runs)]
    record = {
        "mode": args.label,
        "size_mb": round(folder_size(args.target) / 1e6, 1),
        "cold_start_first_s": round(times[0], 3),
        "cold_start_median_s": round(statistics.median(times), 3),
        "cold_start_max_s": round(max(times), 3),
        "peak_rss_mb": peak_rss_mb(exe),
        "runs": args.runs,
        "platform": f"{platform.system().lower()}-{platform.machine().lower()}",
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
    }
    text = json.dumps(record, indent=2)
    print(text)
    if args.json:
        args.json.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
