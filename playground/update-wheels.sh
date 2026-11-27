#!/bin/sh
# Rebuild the wheels the playground installs in the browser. Run from the repo root.
set -e
rm -f playground/wheels/*.whl
uv build --wheel -o playground/wheels
rm -f playground/wheels/.gitignore
python3 -m pip download "sqlglot==${SQLGLOT_VERSION:-30.35.0}" --no-deps --only-binary=:all: -d playground/wheels
python3 -c "import json,os;print(json.dumps(sorted(f for f in os.listdir('playground/wheels') if f.endswith('.whl'))))" > playground/wheels/manifest.json
