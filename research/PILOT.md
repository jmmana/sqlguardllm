# Bounded Claude integration pilot

This is a prepared experiment, not a measured model result. The library itself
does not require a Claude account or make API calls.

The pilot uses the first 20 distinct SQL strings for one Spider development
schema, in published order. Each question has a clean prompt and a paired prompt
with an instruction injected into untrusted schema documentation. Original
annotations, a pinned public database mirror, source fingerprints, licensing
and the tenant augmentation are in [datasets/](datasets/). Gold task results
and policy results are scored separately against a physically restricted DB.
This is not a Spider leaderboard or production attack-success estimate.

## Credential protection in a public repository

Create an **Actions Secret** named `ANTHROPIC_API_TOKEN` under Settings → Secrets
and variables → Actions. Put the API key in its Secret field. The name is
public in the workflow; its value is not committed. Do not add the actual key to
source, a tracked `.env`, README, issue, screenshot or workflow YAML.

The workflow has only a manual `workflow_dispatch` trigger, requires the owner
`jmmana` as both actor and triggering actor, and runs only from `main` in
`jmmana/sqlguardllm`. It does not accept a caller-supplied ref or SQL. Forks,
comments and pull requests cannot trigger paid inference. The API credential
is present in the inference step only; installation, dry-run and upload steps
do not receive it. Checkout does not persist GitHub credentials. Actions are
pinned to reviewed commit SHAs.

The client also refuses non-owner GitHub Actions execution. It uses one fixed
Anthropic HTTPS endpoint and model, blocks redirects, has no automatic retries,
and never writes the credential or request headers to results. API error
messages retain status/endpoint only. Synthetic/public prompts and model
outputs are the intended public research artifacts.

These controls do **not** make repository write access harmless: a writer can
change workflow/code to misuse or extract secrets. GitHub's log masking is not
a security boundary. Keep write access restricted; a dedicated project key and
provider-side spending limits further bound its permissions. If a value is ever
published, revoke/replace it; deleting a line from the current tree is insufficient.

## Spending bounds

The user authorized **US$10 total**. The fixed pilot is 40 inference calls,
up to 768 output tokens each, without tools or extended thinking. Token
preflight, a conservative input allowance and a reservation per sent request
bound the run. The workflow permits only the first four run numbers and first
attempts, each reserving less than US$2.50; rerunning a job cannot silently
repeat paid calls. A fresh manual run is a deliberate owner action.

The script records its exact reservation and the standard model prices checked
on November 26, 2026. Reserved cost is a conservative accounting bound at those
rates, not an Anthropic invoice or an account-wide billing setting. Other
programs using the same key are outside this experiment's budget ledger.

## Execution and status

The file must be merged into the default branch before GitHub allows manual
dispatch. Then select **Actions → Claude pilot (manual, owner only) → Run
workflow**, choosing `main`. If the secret is absent or the fixed model is
unavailable, it stops before inference. Missing credentials are never replaced
by an instruction to paste a key into chat.

To inspect the fixture and planned budget locally without credentials/calls:

```bash
python research/claude_pilot.py
```

An authorized local researcher can explicitly run `--execute` with the secret
in their process environment. Results are written to a fresh output directory;
existing observations are never overwritten. `observations.json` contains
prompts, responses, decisions, rows and separate task/policy outcomes.
`metadata.json` records completion or a sanitized stop reason. Partial and
failed runs must not be presented as a completed 40-call experiment.
