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
observations, and writes a one-page PDF with a detailed CSV. Explicitly documented diagnostic
exclusions may be supplied in an external JSON file; their costs remain in cumulative spending.

The table separates completion, expected route, positive function-call expectations, explicit
prompt assertions, TTFT, full response latency and calculated cost. Latency uses completed
conversations; quality percentages exclude provider/context rejections. Missing expectations are
unknown rather than a perfect score. The full transcript review is in a separate private directory.

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
