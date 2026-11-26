# Agent workflow

Follow [CONTRIBUTING.md](CONTRIBUTING.md) for setup, design principles and validation commands.

## Finish one issue at a time

- Check open PRs and dependencies before starting; reuse existing work instead of duplicating it.
- Use one branch and one focused PR per cohesive issue, based on the current `main`.
- Keep one active implementation branch per agent. Finish its checks and integration before starting another implementation.
- A branch waiting for review may remain while its PR is open. Do not recreate or reuse a merged branch.

## Use the Claude API for agent issue development

- Maintainer-directed coding agents obtain a Claude API proposal or code review for each issue through the [manual issue helper](docs/CLAUDE_ISSUE_HELPER.md).
- Use the configured GitHub Actions secret `ANTHROPIC_API_TOKEN`. Never expose its value in files, prompts, logs, issues or PRs.
- Respect the spending limit already authorized for the task, including earlier calls. The helper's USD 5 per-run ceiling is not a new spending authorization.
- Preserve the run's `manifest.json` and proposal. Record the workflow link, model, token usage and estimated cost in the PR.
- Review the proposal against the code, apply accepted changes and run the relevant tests. Model output is not validation evidence.
- Claude assists development; preserve the guard's deterministic, offline runtime.

## Complete and verify

- Meet every acceptance criterion and pass the applicable CI checks before merging and closing the issue.
- Verify the affected database or client when the change requires it; distinguish local skips from executed checks.
- If work remains, report it and keep the issue open. Do not close an issue merely because a PR exists.

## Clean up branches

- GitHub automatically deletes merged PR head branches. Confirm cleanup after integration and prune local remote-tracking refs.
- For older branches, first verify the current head exactly matches a merged PR's head, or all its commits are in `main`, and that no open PR or pending work needs the branch.
- Keep `main`, branches with open PRs and branches with work that has not been integrated.
- Age alone is not a reason to delete a branch. Inspect an uncertain branch rather than discarding its work.
