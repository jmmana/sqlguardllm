"""Native PostgreSQL backend over documented single-user stdin/stdout.

This avoids sockets in restricted environments. It is not a client/server,
concurrency or production-authorization benchmark. Each fixture has its own
cluster; only synthetic data is loaded. pgserver supplies native binaries.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import tempfile
from pathlib import Path


def binaries() -> tuple[Path, Path]:
    spec = importlib.util.find_spec("pgserver")
    if spec is None or spec.origin is None:
        raise RuntimeError("Native study needs the optional pgserver==0.1.4 wheel")
    root = Path(spec.origin).parent / "pginstall/bin"
    return root / "postgres", root / "initdb"


class NativeSession:
    def __init__(self, sql: str) -> None:
        postgres, initdb = binaries()
        self.temp = tempfile.TemporaryDirectory(prefix="sqlguard-native-study-")
        self.counter = 0
        subprocess.run(
            [str(initdb), "-D", self.temp.name, "--no-locale", "--encoding=UTF8", "--auth=trust"],
            check=True,
            text=True,
            capture_output=True,
            timeout=45,
        )
        self.proc = subprocess.Popen(
            [
                str(postgres),
                "--single",
                "-D",
                self.temp.name,
                "-c",
                "shared_buffers=16MB",
                "-c",
                "statement_timeout=10000",
                "postgres",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self.command(sql, readonly=False)

    def command(self, sql: str, *, readonly: bool = True) -> str:
        self.counter += 1
        marker = f"__study_done_{self.counter}__"
        statement = sql.replace("\n", " ")
        prefix = "BEGIN READ ONLY;\n" if readonly else ""
        self.proc.stdin.write(
            prefix + statement + "\nROLLBACK;\n" + f"SELECT '{marker}' AS __done;\n"
        )
        self.proc.stdin.flush()
        output = []
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("Native PostgreSQL worker exited before completing a query")
            output.append(line)
            if f'__done = "{marker}"' in line:
                break
        text = "".join(output)
        if "ERROR:" in text or "FATAL:" in text:
            match = re.search(r"(?:ERROR|FATAL):\s*([^\n]+)", text)
            raise RuntimeError(match.group(1) if match else text[-250:])
        return text

    def query(self, sql: str, fetch_limit: int = 9) -> list:
        # row_to_json preserves column order and duplicate names. Retain pairs
        # when decoding, so duplicate projection names never overwrite values.
        wrapped = (
            "SELECT COALESCE(json_agg(row_to_json(q)), '[]'::json) AS __payload "
            f"FROM ({sql.strip().rstrip(';')}) AS q;"
        )
        output = self.command(wrapped)
        match = re.search(r'__payload = "(.*?)"\s+\(typeid', output, re.S)
        if match is None:
            raise RuntimeError("Native worker did not return a JSON result")
        rows = json.loads(match.group(1), object_pairs_hook=lambda pairs: [v for _, v in pairs])
        return rows[:fetch_limit]

    def close(self) -> None:
        self.proc.stdin.close()
        self.proc.wait(timeout=15)
        self.proc.stdout.close()
        self.temp.cleanup()


class NativeBackend:
    name = "postgres_native_single"

    def __init__(self, fixtures: dict[str, str]) -> None:
        self.sessions = {key: NativeSession(sql) for key, sql in fixtures.items()}
        self.engine_version = self.sessions["full"].query("SELECT version()", 1)[0][0]

    def query(self, key: str, sql: str, *, role=None, fetch_limit: int = 9) -> list:
        if role is not None:
            raise ValueError("Single-user observations do not claim native role enforcement")
        return self.sessions[key].query(sql, fetch_limit)

    def close(self) -> None:
        for session in self.sessions.values():
            session.close()
