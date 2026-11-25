# Security policy

sqlguardllm is a security component: **a way to make `check()` accept SQL that breaks one of its guarantees is a vulnerability**, even if it looks like a parser quirk. The guarantees are listed in the [threat model](docs/THREAT_MODEL.md#what-sqlguardllm-guarantees).

## Reporting a vulnerability

**Please don't open a public issue, discussion or pull request.** Use GitHub's private reporting:

1. Go to the [**Security**](https://github.com/jmmana/sqlguardllm/security) tab.
2. Click **Report a vulnerability**.
3. Include the dialect, the policy, the SQL, what `check()` returned and what you expected. A minimal reproduction is ideal.

What happens next:

| When | What |
|---|---|
| ≤ 3 business days | Acknowledgement |
| ≤ 10 business days | Assessment and a fix plan |
| Fix released | A GitHub Security Advisory, a CVE when appropriate, and the case added to the adversarial corpus |

We credit reporters in the advisory unless you prefer to stay anonymous.

## In scope

- Bypasses of any rule: writes, multiple statements, tables or columns outside the policy, denied functions, row filters, row limit.
- Parser differentials: SQL that sqlglot and the real engine interpret differently in a way that defeats a rule.
- Unsafe binding of context values into row filters.

## Out of scope

- Risks listed as out of scope in the [threat model](docs/THREAT_MODEL.md#what-it-does-not-protect-against) (query cost, inference through allowed data, missing database permissions).
- Vulnerabilities in sqlglot itself: report those to [sqlglot](https://github.com/tobymao/sqlglot). If one affects sqlguardllm's guarantees, tell us too.

## Supported versions

sqlguardllm is pre-release. From v0.1 on, security fixes go to the latest minor version.
