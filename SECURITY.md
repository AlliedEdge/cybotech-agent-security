# Security Policy

## Project scope

Cybotech is a research and engineering project for demonstrating and measuring defenses against indirect prompt injection in agentic AI systems. It operates entirely on synthetic data within a sandboxed local environment.

**This project is not a production security product.** It does not protect real systems, process real customer data, or send real email. All experiments run against synthetic inputs and simulated tools.

## Supported versions

This project is under active development. Only the latest commit on the `main` branch is maintained.

| Branch | Supported |
|---|---|
| `main` | ✅ Yes |
| All others | ❌ No |

## Reporting a vulnerability

If you discover a security issue in this repository — including secrets accidentally committed, dependency vulnerabilities with a realistic exploit path against this codebase, or a bypass of a documented security invariant — please report it responsibly.

### How to report

**Do not open a public GitHub issue for a security vulnerability.**

> ⚠️ **Maintainer action required:** Configure a private reporting channel before making this repository public. Options include:
> - GitHub's built-in private vulnerability reporting (Settings → Security → Advisories → Enable private reporting)
> - A dedicated security contact email address added here
>
> Until a reporting channel is configured, contact the repository owner directly through GitHub.

### What to include

A useful report includes:

1. A description of the vulnerability and which component it affects
2. Steps to reproduce it (or a proof-of-concept if applicable)
3. The potential impact in the context of this project's stated scope
4. Any suggested mitigations you have identified

### Response expectations

We will acknowledge receipt of a vulnerability report as promptly as we can and will keep you informed of the resolution timeline. We will credit reporters in the relevant commit or advisory unless you prefer to remain anonymous.

## Out of scope

The following are outside the scope of this project's security posture and will not be treated as vulnerabilities:

- Attacks that require controlling application source code, policy configuration, or the database directly (the attacker model excludes these)
- Theoretical prompt-injection scenarios not covered by the documented attack objectives
- Performance issues, availability issues, or denial-of-service without a security impact
- Issues in upstream dependencies that have no realistic exploit path in this codebase
- Missing features described as planned but not yet implemented

## Design limitations

This project acknowledges the following limitations in its security design:

- LLM outputs are probabilistic. An attack that fails on one run may succeed on another with different model behavior.
- The evaluation set (60 cases) is small. Results should not be generalised beyond the documented scenarios.
- Sentinel AI is an advisory component. Its analysis can be wrong. The Policy Enforcement Gateway is the final authority.
- The project does not claim immunity against prompt injection. It demonstrates specific defenses and measures specific outcomes.

These are documented limitations, not reportable vulnerabilities.

## Dependency security

Dependencies will be pinned to specific versions when introduced. Known vulnerable dependencies should be reported using the process above. We do not use automated dependency update bots without review.
