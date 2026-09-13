# Completing a private suite

Install the report extra with `uv sync --extra dev --extra report`. Keep the environment file,
scenario data, budget ledger, original exports and every output outside all Git working trees.

`scripts/run_suite.py` runs one case at a time for each provider. Every case receives an atomic,
per-model budget reservation before network access. It preserves a provider's rolling rate limiter
between cases, supports independent provider workers and records that overlap in the manifest.
Concurrent provider workers are not a same-model load test.

```sh
PYTHONPATH=src python scripts/run_suite.py \
  --data-dir /private/bench-data \
  --config-dir /private/bench-config \
  --env-file /private/bench-secrets/.env \
  --budget-file /private/bench-secrets/budget.json \
  --out /private/bench-output \
  --models model-a,model-b --workers 2
```

Use `--resume-from` for earlier result roots. Resume matches the full scenario hash, source hash,
model key and repetition. A failed model response remains an observation; resuming does not retry
it to obtain a better result. Changed case definitions form a different cohort. Three consecutive
provider failures stop that worker; resolve the account/configuration problem before starting
another diagnostic series. Never treat rate-limit rejections as a model-quality score.

After a completed execution, unused call capacity is released. A second reconciliation verifies
that every transport attempt has a matching record and that all bounds match the reservation.
For completed streams with provider-reported usage, it retains uncached input plus the entire
output allowance. Failed or unmeasured attempts retain their full-context bound. Interrupted
executions, missing attempt logs, mismatched evidence and historical diagnostic margins are not
silently released. These reserves are conservative controls, not invoices.

## Executive report

`scripts/create_onepager.py` takes the suite/configuration, result roots, cost root, budget,
private guard and environment file. It matches current scenario/source hashes, refuses duplicate
observations, and writes an English one-page PDF with a detailed CSV. Explicitly documented diagnostic
exclusions may be supplied in an external JSON file; their costs remain in cumulative spending.

The table separates completion, expected route, positive function-call expectations, explicit
prompt assertions, TTFT, full response latency and calculated cost. Latency uses completed
conversations; quality percentages exclude provider/context rejections. Missing expectations are
unknown rather than a perfect score. The full transcript review is in a separate private directory. Its interface is in English,
while source conversations remain in their original language. External English scenario labels
can be supplied with `--private-scenario-labels`, keyed by scenario hash; this does not change
the source cases or prompts. `--private-project-labels` affects only this private viewer.

The viewer compares all four models at project, common-case and individual-case levels.
Common cases retain quality failures. Per-turn first-text and full-flow timings include local
orchestration and mock responses, excluding quota waits. The anonymized outputs are
`Benchmark-LLM.pdf`, `Detailed-results.csv`, `Case-coverage.csv` and `Node-coverage.json`.

Read these limits alongside the results:

- Scripted users and mock tool outputs evaluate LLM behavior, not production backend quality.
- Native function-call events count; promises in plain text do not.
- Original prompts and node transitions stay intact; assumed platform interfaces are disclosed.
- One run per case provides scenario coverage, not a stable tail-latency or reliability estimate.
- Provider-specific reasoning, caching, tokenization and quotas affect comparisons.
- Accepted context size does not prove instruction retention across the advertised full window.
- Automated language flags require review; unvisited turns are not evidence of an observed leak.
- Calculated token costs and conservative reserves are separate from provider invoices.

The PDF must pass text/metadata publication checks and a visual one-page review before sharing.
Binary reports remain outside the code repository, even when the contents are anonymized.

## Reviewed silent closes

An empty API response is not automatically a failed conversation. An external, source-hash-bound
adjudication policy may identify logical terminal nodes where the original prompt requires silence
after a farewell. The report applies that policy identically to every model, only when all scripted
turns were reached, a farewell was already spoken, a valid route reached the approved terminal,
and the API ended with a normal STOP. Truncated responses, missing turns and transport errors do
not qualify. Shared route maps on logical terminals require explicit review in the policy.

The interpreter never edits raw transcripts, fabricates TTFT or makes extra API requests. It writes
an audit record next to the private conversation review. Empty responses outside the reviewed
condition remain failures. Google and OpenAI-compatible normal STOP representations are treated
equally when yielding to a question already spoken immediately before routing.

## Refreshing a running report

`scripts/refresh_onepager.py --args-file /private/path/report-args.txt`
refreshes the same artifacts as completed case records or review policies change. It invokes
only the local report generator, never an LLM API. The planned count is read from the report. Use `--once` for a single refresh.
The watcher stops when all case dispositions are present or its bounded watch window ends,
and writes `refresh-status.json`. Quota blocks count as recorded dispositions, not successful
conversations. Language signals may still require review. Use `{execution_status}` in an
external report note for an accurate description of recorded versus unrun combinations.

## Consolidated sample statistics and follow-up views

`Model-consolidated.pdf` adds one page per model with arithmetic mean, minimum,
maximum and sample count. `Model-consolidated.csv` includes both all-project and
per-project summaries, with available-case and common-case cohorts. Means use
individual calls, turns or assertions, rather than averages of project medians.
Each row identifies its sampling unit. The viewer exposes the same summary and
explains metric formulas and total-cost scope.

A private `--followup-plan` declares original/replacement run pairs with matching
model, scenario hash and source hash. Replacements are selected regardless of their
outcome. Original evidence and its report remain separately available. This targeted
follow-up view is not an unbiased new first-attempt reliability estimate.
`--historical-runs-file` keeps a previous reasoning profile out of the current view;
the generator rejects mixed profiles under one model. Profiles are derived from
recorded model settings. Reducing reasoning effort does not imply disabling thinking.

A stopped serial execution may be reconciled only through an explicit termination
and code audit using `reconcile_stopped_serial`. It requires single-case, single-worker,
one-attempt execution, proof that the process stopped, and a matching review of the
runner, storage and retry-free transport code. The full cost bound is retained for
every recorded call plus one possible unrecorded in-flight request. The original
manifest remains incomplete, evidence is hashed, and the ledger update is locked
and idempotent. Parallel, unverified or changed evidence cannot use this path.

Completed executions already authenticated by `reconcile_usage` may undergo an
explicit `reconcile_finished_output` review. It releases unused output allowance
only when complete provider counts and unchanged evidence are available. It retains
uncached input plus completion **and** reasoning tokens, deliberately counting
reasoning twice when it is already included. Failed attempts, missing usage,
interrupted executions and unaudited historical margins keep their prior reserves.
The separate audit is locked and idempotent; it never changes spending limits or
claims to settle a provider invoice.
