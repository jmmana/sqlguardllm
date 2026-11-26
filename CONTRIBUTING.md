# Contributing to sqlguardllm

Thanks for helping make LLM apps safer to connect to real databases. Contributions of every size are welcome, from a single attack case to a new dialect.

*¿Hablas español? Issues y PR en español son bienvenidos.*

## Ways to contribute

| You want to… | Do this |
|---|---|
| Report a bug | Open a [bug report](https://github.com/jmmana/sqlguardllm/issues/new?template=bug_report.yml) |
| Report a **bypass** (SQL that passes `check()` but shouldn't) | **Don't open a public issue.** Report it privately: [SECURITY.md](SECURITY.md) |
| Add an attack case we already handle | Open an [attack case](https://github.com/jmmana/sqlguardllm/issues/new?template=attack_case.yml) or a PR to `tests/corpus/` |
| Propose a feature or a change to the API | Start in [Discussions](https://github.com/jmmana/sqlguardllm/discussions), then a [feature request](https://github.com/jmmana/sqlguardllm/issues/new?template=feature_request.yml) |
| Pick up work | Look at [good first issues](https://github.com/jmmana/sqlguardllm/labels/good%20first%20issue) and [help wanted](https://github.com/jmmana/sqlguardllm/labels/help%20wanted); comment that you're taking it |

## Good first contributions

- **Attack cases.** Every case in the [attack gallery](docs/ATTACK_GALLERY.md) is a test. Cases for dialects you use every day (T-SQL, Oracle, Snowflake, BigQuery, ClickHouse…) are especially valuable.
- **Denied functions.** Know a function in your database that reads files, calls the network, sleeps or changes state? Add it to the dialect's list with a test.
- **Messages.** Improve or translate the human-readable violation messages.

## Development setup

```bash
git clone https://github.com/jmmana/sqlguardllm.git
cd sqlguardllm
uv sync                      # Python 3.10+, installs dev tools
uv run pytest                # unit tests + adversarial corpus
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy
```

## How work is described

Issues that describe work follow the same shape, so anyone can pick them up without asking:

- **Goal**: one result and why it matters.
- **Acceptance criteria**: checkboxes, each verifiable with a command or a test.
- **Relevant files**: where to work and which tests must keep passing.
- **Size**: S (half a day), M (1–2 days), L (3–5 days).
- **Notes**: dependencies (`Depends on #N`) and links to the design.

The [Claude issue helper](docs/CLAUDE_ISSUE_HELPER.md) can prepare a proposal for maintainers to review.

## Pull requests

1. Fork, then branch from `main`: `feat/<short-name>`, `fix/<short-name>` or `corpus/<short-name>`.
2. Keep PRs focused: one issue, one result.
3. Add tests for new behavior. A new rule needs at least one case that must be rejected **and** one that must keep working.
4. `ruff`, `mypy` and `pytest` green locally and in CI.
5. If behavior changes, update [docs/DESIGN.md](docs/DESIGN.md) in the same PR and add a line to the changelog.
6. Fill in the PR template and link the issue (`Closes #N`).

A maintainer reviews within a few days. Security-relevant changes (anything in the rules or the rewrite) need a careful review and may take longer.

## Design principles

When in doubt, these decide:

1. **Fail closed.** Rejecting a valid query is a bug; accepting a dangerous one is a vulnerability.
2. **Decide on the syntax tree, never on the text.**
3. **One runtime dependency** (sqlglot). New dependencies need a very good reason.
4. **Stable violation codes.** Messages can improve; codes don't change within a major version.
5. **Deterministic and offline.** No network, no LLM, no randomness.

## License of contributions

sqlguardllm is licensed under [Apache-2.0](LICENSE). By submitting a contribution you agree that it is licensed under the same terms (Apache-2.0, section 5). No CLA is required.

## Code of conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). Be kind; critique ideas, not people.

## Releasing

Maintainers only. Every release goes to PyPI, npm and NuGet together, with one version.

1. Set the version in `pyproject.toml` (for example `0.6.0a1`) and the matching one in `clients/node/package.json` and `<Version>` of `clients/dotnet/src/SqlGuardLlm/SqlGuardLlm.csproj` (`0.6.0-alpha.1`; `b1` is `-beta.1`, `rc1` is `-rc.1`), then run `uv lock` and `npm install --package-lock-only` in `clients/node`.
2. Move the `Unreleased` notes in `CHANGELOG.md` under `## [0.6.0a1] - YYYY-MM-DD`.
3. Merge to `main`, then tag: `git tag -a v0.6.0a1 -m "sqlguardllm 0.6.0a1" && git push origin v0.6.0a1`.

The `Release` workflow checks that tag, versions and changelog agree, runs the tests, and publishes to PyPI, npm and NuGet with Trusted Publishing (no tokens). npm gets the dist-tag from the version: before 1.0 every release is `latest`; from 1.0 on, prereleases get `alpha`, `beta` or `rc` and final releases `latest`. If npm holds a release for approval (staged publishing), approve it at npmjs.com → Staged Packages.

