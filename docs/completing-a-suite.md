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
