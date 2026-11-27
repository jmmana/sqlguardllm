#!/bin/sh
# One-time bootstrap (owner): publish a 0.0.0 placeholder for each engine package name so that
# npm lets you configure Trusted Publishing on them. Real releases are published by CI.
#   npm login            # 2FA on
#   sh scripts/bootstrap-npm-engines.sh
# Placeholders go out under the `bootstrap` tag, so `latest` is never touched.
set -eu
for platform in linux-x64 linux-arm64 macos-arm64 macos-x64 windows-x64; do
  name="sqlguardllm-engine-$platform"
  if npm view "$name" version >/dev/null 2>&1; then
    echo "$name already exists, skipping"
    continue
  fi
  dir=$(mktemp -d)
  cat > "$dir/package.json" <<JSON
{
  "name": "$name",
  "version": "0.0.0",
  "description": "Placeholder. The real engine package is published by the sqlguardllm release workflow.",
  "license": "Apache-2.0",
  "repository": { "type": "git", "url": "git+https://github.com/jmmana/sqlguardllm.git" },
  "publishConfig": { "access": "public" }
}
JSON
  echo "publishing $name@0.0.0 (tag bootstrap)"
  (cd "$dir" && npm publish --access public --tag bootstrap)
  rm -f "$dir/package.json"
  rmdir "$dir"
done
echo "Next: for each of the five packages and sqlguardllm, npmjs.com -> Settings -> Trusted Publisher:"
echo "  GitHub Actions, user jmmana, repository sqlguardllm, workflow release.yml, environment npm"
