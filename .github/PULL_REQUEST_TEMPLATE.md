## Summary

<!-- One paragraph: what this PR does and why. -->

## Type of change

- [ ] Feature (new functionality)
- [ ] Bug fix
- [ ] Security fix / hardening
- [ ] Documentation
- [ ] Refactor (no behaviour change)
- [ ] Chore (deps, tooling, CI)

## Security impact

<!-- Required for any change to: memory_guard/, policy_gateway/, sentinel/, retrieval/, domain/policies/, infrastructure/database/ -->
<!-- If not applicable, write "N/A". -->

- Which security invariants does this change affect?
- What is the failure mode if this code has a bug?
- How was the failure path tested?

## Testing

- [ ] Unit tests added or updated
- [ ] Integration tests added or updated
- [ ] Security invariant tests pass (`make backend-test`)
- [ ] Frontend tests pass (`make frontend-test`)

<!-- Describe what the tests prove, not just that they pass. -->

## Checklist

- [ ] No real secrets, credentials, or PII in any committed file
- [ ] No fabricated test results or benchmark values
- [ ] Linting passes (`make lint`)
- [ ] Type checking passes (`make typecheck`)
- [ ] README or docs updated if behaviour changed
- [ ] `.env.example` updated if new environment variables were added
- [ ] For security-critical code: both team members have reviewed
