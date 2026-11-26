"""Generate the policy reference page from schema/policy.schema.json (mkdocs-gen-files)."""

import json
from pathlib import Path

import mkdocs_gen_files

schema = json.loads(Path("schema/policy.schema.json").read_text(encoding="utf-8"))
required = set(schema.get("required", []))


def type_of(spec: dict) -> str:
    t = spec.get("type", "any")
    return " | ".join(t) if isinstance(t, list) else str(t)


lines = [
    "# Policy reference",
    "",
    "Generated at build time from `schema/policy.schema.json`. Do not edit by hand.",
    "",
    schema.get("description", ""),
    "",
    "| Field | Type | Required | Description |",
    "|---|---|---|---|",
]
for name, spec in schema["properties"].items():
    desc = spec.get("description", "").replace("|", "\\|").replace("\n", " ")
    lines.append(f"| `{name}` | `{type_of(spec)}` | {'yes' if name in required else 'no'} | {desc} |")
lines += ["", "## Full schema", "", "```json", json.dumps(schema, indent=2), "```", ""]

with mkdocs_gen_files.open("policy-reference.md", "w") as f:
    f.write("\n".join(lines))
