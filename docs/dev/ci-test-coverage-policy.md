# CI test and executable-code coverage policy

`config/ci-test-policy.yaml` is the single source of truth for the dynamic
pytest matrix, temporary allowed failures, and coverage floors. The CI runner
rejects unknown fields, unsafe paths or dependency strings, stale skill IDs,
expired exceptions, and policy/artifact mismatches.

## Test-presence gate

Every skill with executable `skills/<id>/scripts/**/*.py` code must have at
least one canonical `test_*.py` file in `scripts/tests/` or `tests/`.
Files under a `tests/` directory and `__init__.py` are not executables.
This applies to production, beta, experimental, and deprecated skills so that a
status change cannot make an untested executable disappear from CI. An empty
test directory does not satisfy the gate.

Script-free production skills must declare `knowledge_only: true` in
`skills-index.yaml`. The marker is invalid for non-production skills and for a
skill that contains executable Python scripts at any depth under `scripts/`.

## Coverage measurement

Coverage percentages include executable code and exclude all files matching
`*/tests/*`:

- Core risk, financial-math, and state-management skills have an 85% target:
  `position-sizer`, `futures-position-sizer`, `trader-memory-core`, and
  `drawdown-circuit-breaker`.
- Every other executable skill has a 70% target, regardless of status.
- The repository aggregate includes every executable skill plus code under the
  root `scripts/` directory and has a 75% target.

The aggregate step publishes `per-skill-coverage.json`,
`per-skill-coverage.md`, the raw per-row coverage JSON, and the combined
repository coverage JSON. It writes the reports before returning a failure for
any floor violation, so failed CI still preserves actionable evidence.

## Ratchet and waiver rules

Coverage that is already at target has no waiver and may not regress below the
target. A below-target baseline may temporarily declare an effective floor,
but every waiver must include all of the following in versioned config:

- the final target and a lower current floor;
- an ISO `expires_on` date;
- a linked GitHub issue number;
- a non-empty reason.

The loader fails closed after the expiry date. Lowering a floor or extending an
expiry requires fresh measured evidence and explicit review; it is not routine
CI maintenance. Once a skill reaches its target, remove its waiver instead of
resetting the baseline.

The 2026-08-10 baseline uses executable code only. Across the current 69
executable skills plus root repository scripts, Linux CI measured 35,031
covered statements out of 48,073 (72.870%); local Python 3.9 validation
measured 35,429 out of 48,073 (73.698%). The temporary repository effective
floor is therefore the lower cross-platform baseline rounded down to 72%, with
a 75% target. Per-skill floors follow the same cross-platform rule. All current
waivers expire on 2026-10-31 and link to Issue #293.

### Burn-down schedule

1. By 2026-08-31, prioritize skills below 50% and add happy-path, boundary,
   error-path, and fail-closed tests around production code.
2. By 2026-09-30, bring every remaining non-core executable to at least 70%
   and remove its waiver as soon as it passes.
3. By 2026-10-31, bring the four core skills to at least 85%, raise the
   executable-code repository aggregate to at least 75%, and remove all
   coverage waivers.

`allowed_failures` are separate from coverage waivers. They permit a matrix row
to be non-blocking only when the entry has its own future expiry, linked issue,
and reason. There are currently no allowed-failure rows; `theme-detector` is a
blocking suite.
