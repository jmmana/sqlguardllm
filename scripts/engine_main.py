"""Entry point of the standalone engine binary: the same as `sqlguardllm engine --stdio`.

It imports only the engine, so the binary carries sqlguardllm, sqlglot and the standard library.
"""

import sys

from sqlguardllm.engine_stdio import run

if __name__ == "__main__":
    if sys.argv[1:] not in ([], ["engine", "--stdio"], ["--stdio"]):
        print("usage: sqlguardllm-engine [--stdio]", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(run(sys.stdin, sys.stdout, sys.stderr))
