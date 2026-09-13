# llm-bench

A Python toolkit for benchmarking conversational LLMs with **your own prompts, tools and conversation flows**. Compare latency, token usage, calculated costs, native function calls and explicit scenario rules. Inspect the simulated conversations in a private, offline HTML viewer.

The engine does not contain customer prompts or project-specific scoring rules. Projects use anonymous aliases (`project_1`, `project_2`, …); prompts, credentials, scenarios and raw results stay outside Git. MIT licensed.

## Install

Python 3.11+ and [uv](https://docs.astral.sh/uv/):

```sh
uv sync --no-editable --extra dev --extra report
git config core.hooksPath .githooks
uv run --no-editable vbench --help
```

## Try it without an API key or API charges

The included fixtures are fictional. Create a directory outside every Git working tree:

```sh
mkdir -p ../bench-private/scenarios
cp tests/fixtures/neutral_scenario.yaml ../bench-private/scenarios/
uv run --no-editable vbench import-bundle \
  --input tests/fixtures/neutral_graph.json --out ../bench-private/bundles
uv run --no-editable vbench run \
  --data-dir ../bench-private --offline-demo --repetitions 1 --no-warmup
```

The offline provider returns canned text. It checks installation, orchestration and artifact generation; it does **not** demonstrate real model performance and will fail the example's expected tool calls. Automated tests separately exercise successful and failed native tool flows. Private outputs include `manifest.json`, `calls.jsonl`, `runs.jsonl`, `transcripts/`, CSV summaries and Markdown conversation histories.

## Bring your own project

Use [the neutral JSON example](tests/fixtures/neutral_graph.json) and [scenario example](tests/fixtures/neutral_scenario.yaml). There is no dependency on a particular platform's export format.

A bundle defines:

- Original system and node prompts, the starting node and tool JSON Schemas.
- `composition: all_nodes` for a graph, or `single_node` for independent segments.
- `nodes[].transitions`: allowed destination alias → node ID.
- `routing.tool_name` and `routing.argument_name`: the actual routing interface to advertise.
- `nodes[].tool_transitions`: business tool name → node ID, followed only after a valid call and successful simulated result.
- `allowed_languages`: two-letter language codes, such as `[en]`, `[es]` or `[en, es]`. An empty list disables language checking.
- `orchestration_language`: `en` or `es` for added transport instructions. Original prompt text can be in any language.
- Optional `routing.transport_note`: an explicitly declared compatibility instruction for your source platform.

`import-bundle` validates the graph and schemas, fingerprints the original input and saves an external bundle. The older `vbench extract` command remains an adapter for the documented graph-export layout; other exports should be converted to the neutral schema. This is not an automatic parser for every platform or a tool that infers hidden business rules from prose.

Scenarios specify simulated user turns, branch-dependent wording (`content_by_node`), variables, mock tool responses, state updates, prerequisites, expected function names/argument subsets/counts, forbidden tools, per-turn limits, terminal conditions and explicit assertions. Expected flow milestones can use node IDs or names. Intermediate steps are allowed; unreachable milestone sequences are rejected.

**The expected path is an assertion, not an instruction to force the model through that path.** Changing a scoring rule requires changing scenario configuration. Backend handlers from exports are never executed.

## Bring your own models

Add any deployment supported by an adapter to your **external** `models.yaml`; model IDs are not allowlisted. Use a separate key for each deployment/reasoning profile you want to compare:

```yaml
models:
  my-deployment-low:
    display_name: My deployment · low
    enabled: true
    provider: openai_compat
    base_url: https://YOUR_ENDPOINT/v1
    model_id: YOUR_MODEL_ID
    api_key_env: MY_LLM_API_KEY
    context_window: 32000  # Replace with your deployment's verified limit.
    supports_tools: true
    supports_system: true
    extra: {reasoning_effort: low}  # Only if this endpoint supports it.
    last_verified: PENDING
```

`openai_compat` supports the Chat Completions streaming protocol. `google_genai` supports the Google Gen AI adapter. A local model server can use either compatible protocol. Other protocols require an adapter implementing [LLMProvider](src/llm_bench/providers/base.py), installed through the `llm_bench.providers` Python entry-point group. The factory receives `(model, timeouts)`; SDK/network work must be deferred to `stream_chat`. Adapters must report streaming events and normalize usage correctly. See [extension guidance](docs/extending.md).

Compatibility depends on the deployment: tool support, system messages, usage fields, output limits and reasoning parameters must be verified. The bundled model catalog is a set of examples, not the supported-model boundary. `low` is not a universal “thinking off” setting.

Copy `.env.example` outside Git and add your environment-variable names there. Use rates covering the applicable context/usage tier; a static price table does not automatically discover pricing tiers. Add verified input/output/cache prices under the same model keys in `pricing.yaml`; rates are USD per million tokens. Copy `config/bench.yaml` to the external configuration directory and adjust limits, repetitions, retries and concurrency.

```sh
uv run --no-editable vbench doctor --config-dir ../bench-private/config --env-file ../bench-private/.env
uv run --no-editable vbench run --data-dir ../bench-private \
  --config-dir ../bench-private/config --models my-deployment-low --dry-run
```

`doctor` is local unless `--online` is supplied. `--dry-run` plans requests and budget reservations without inference. Create the ledger with `budget-init` below before starting an online run. Real runs use a persistent external budget ledger with global and per-model pools. Configure your own limits; never reset a ledger to bypass already reserved spending. These are local application controls, not account-wide provider billing caps. Unknown pricing blocks inference. Retried or interrupted calls may retain conservative reservations until supported by usage evidence.

## Set total and per-model budgets before online tests

Initialize a **new** external ledger once. Budget keys must exactly match your model catalog keys:

```sh
uv run --no-editable vbench budget-init \
  --budget-file ../bench-private/budget.json \
  --total 30 \
  --model-limit my-deployment-low=20 \
  --model-limit my-other-model=15 \
  --per-operation 10
uv run --no-editable vbench budget-status --budget-file ../bench-private/budget.json
```

This permits at most USD 30 across the application, at most USD 20 for the first model and USD 15 for the second, with at most USD 10 reserved by one operation. The shared ceiling still applies: both models cannot spend their individual maximums if that would exceed USD 30. A model cannot borrow another model's unused allowance. An unlisted model has no budget authorization. `--per-operation` is optional and defaults to the total ceiling.

Use the same ledger for every related benchmark, online diagnostic and worker:

```sh
uv run --no-editable vbench run --data-dir ../bench-private \
  --config-dir ../bench-private/config --env-file ../bench-private/.env \
  --budget-file ../bench-private/budget.json \
  --models my-deployment-low,my-other-model --repetitions 1 --no-warmup
```

Before transport, the application atomically reserves a conservative amount against **all three** limits: total, per-model and per-operation. Each request/retry is also checked against its operation reservation. Concurrent workers using the same ledger cannot independently reuse the same balance. Built-in adapters reject advanced parameters that override reserved output limits, select another model, request multiple candidates or enable hidden automatic tool rounds. A request that would exceed the authorized reserve is rejected before transport; `--yes` does not bypass this check.

`budget-status` shows limits, reserved amounts and remaining application capacity. Reservations can exceed calculated consumption because they retain room for full context/output, failures and unknown usage. Completed executions may release unused capacity only with supporting records; interrupted or unverified attempts retain their bounds. Initialization refuses to overwrite an existing ledger, including one with prior spending. Never delete/reset the file or create a second ledger to continue the same budget.

These controls rely on correct deployment limits, prices, token accounting and compliant adapters. They do not cap other applications, console activity, taxes or unmodeled provider fees. Use provider-side billing limits as an additional control where available. Zero-cost offline tests make no provider calls and do not consume the ledger. For a complete walkthrough of prompts, credentials, pricing and limits, see [Configuration from scratch](docs/configuration.md).

## Conversation modes and context

- `active_node`: start at the actual starting node, preserve history, apply only allowed transitions and load each new node prompt before continuing.
- `full`: include all node prompts while retaining the active node's permissions; a declared stress-test condition.
- `subset`: restrict available node prompts to an explicit selection; record out-of-subset transitions.
- Independent segments: select `segment` in each scenario; add `--all-segments` to include non-reference segments.

Full prompts are not silently shortened. Context checks include prompts, history, tool schemas and output allowance. Token estimation is approximate across tokenizers; provider-reported acceptance is separate evidence. Use `vbench audit-context` to compare configured limits, request estimates and recorded accepted inputs. Context capacity, rate limits and rule-following quality are different measurements.

## What the metrics mean

| Metric | Measurement and interpretation |
| --- | --- |
| TTFT | API request start to the first nonempty text or tool delta. Separate reasoning events do not start this clock's endpoint. |
| First user-facing text | Simulated user-turn start to the first assistant text, including preceding LLM/tool rounds. It is not the same as TTFT. |
| Full latency | API request start to the end of the response stream. |
| Flow latency | Duration of a simulated user turn across its LLM calls, orchestration and mock results; not the duration of the entire conversation. |
| First native tool delta | Request start to the first native function-call name or argument delta. |
| Expected tools | Passed positive expectations / applicable positive expectations. Requires a native call, correct name, schema-valid arguments matching the expected subset, and specified count in the specified turn. Unreached positive expectations fail. |
| Explicit case rules | Passed programmed assertions / applicable assertions: required or prohibited patterns, length, expected active node, forbidden calls, tool limits and tool-before-text ordering. Does not grade every instruction semantically. |
| Ordered flow milestones | Share of applicable conversations whose actual path contains the expected nodes in order. Extra steps are allowed; repeated milestones matter. |
| Complete conversations | Completed / evaluable conversations. Completion does not imply all quality checks passed. |
| Evaluable coverage | Evaluable / planned observations. Quota, provider and context blocks are shown separately from quality failures. |
| Native / invalid calls | Counts of native function attempts and calls rejected by schema, availability, routing or prerequisites. Text-protocol emulation is separately recorded and cannot satisfy a native-call expectation. |
| Language flags | Heuristic language spans outside the allowed language set, with confirmed/pending human-review decisions. A missing flag is not proof of language correctness. |
| Tokens and throughput | Provider-reported input, output, cached and reasoning tokens when available; otherwise estimates are labeled. Token generation speed and timing populations are recorded separately. |
| Calculated cost | Usage × configured input/output/cache rates. Reports distinguish total known cost, cost per API call and cost per conversation. Missing costs are not zero and calculations are not invoices. |
| Quota wait | Local pacing delay, excluded from reported call/turn latencies and shown separately. |
| Accepted input size | Largest or distributed provider-measured accepted input. Does not prove quality at the full advertised context window. |

Consolidated reports show **mean, minimum, maximum and sample count N**, calculated from individual samples rather than averages of project medians. Timing uses complete conversations; quality includes evaluable failures; known costs also include recorded portions of incomplete conversations. Project/scenario tables identify when they use medians. Common-case comparisons intersect evaluable observations across the selected models, retaining quality failures. For detailed samples and limitations, consult the generated report.

## Compare projects and inspect conversations

Install the `report` extra and run from the repository checkout:

```sh
uv run --no-editable python scripts/create_onepager.py \
  --data-dir ../bench-private --config-dir ../bench-private/config \
  --run-root ../bench-private/results --out ../bench-private/deliverables
```

Use `--models key-a,key-b` to select any number of catalog entries. Multiple `--run-root` options are supported. The report rejects duplicate observations or mixed recorded model profiles: select explicit exclusions/replacements or configure distinct model keys. Add `--include-synthetic` only for fixture runs; their reports are visibly labeled.

Outputs: a compact PDF (paginated for larger matrices), a consolidated PDF per model, CSV metrics and an offline `conversation-review/Conversaciones.html` viewer. Select **All projects** for global statistics or **All simulated scenarios** for the selected project. A specific scenario displays every selected model's recorded conversation side by side. `--baseline-summary` adds a separate initial/current statistics comparison; it does not merge historical profiles into current scores. `--profile-notes-file` adds audited context.

The output directory also contains **`Benchmark-shareable.html`**, a single offline file with anonymous project/scenario selectors, numeric results, consolidated statistics and embedded initial/current profile comparisons. It excludes histories, prompts, tool arguments, original labels and free-text scenario descriptions from the file itself. Model names and reasoning-profile labels remain and are checked by the publication scanner; configure `--guard` and `--env-file` to check your private names, corpus and credential values before sharing. Neither HTML needs external assets or a server. Hover over, focus or tap a metric name to read its definition, measurement method and sample scope. Press Escape to dismiss the explanation. The private conversation HTML still contains sensitive material and is not the shareable export.

Optional `--cost-root`, `--budget-file`, `--guard`, `--env-file`, `--private-project-labels` and `--private-scenario-labels` support private workflows. Labels and transcripts stay in the private viewer. Review any result before sharing it; anonymous aliases alone do not remove sensitive content.

## Privacy, reproducibility and limits

- Real input and output paths must be outside **every** Git working tree. Files use restrictive local permissions; environment credential values are redacted.
- Commit/push hooks scan for credentials and optionally private names and source fragments through an external guard. GitHub CI runs the generic scanner; your private corpus is never uploaded for CI.
- Request/source/scenario fingerprints and experiment manifests preserve provenance. Prompt transformations, reasoning changes, cache state, retry policy and concurrency can change results.
- Tools use deterministic mocks; live backend latency, ASR, TTS and real telephony are outside scope. User turns are scripted, with configurable branch wording; there is no universal autonomous user simulator.
- No automatic semantic judge, production-performance guarantee, universal export importer or automatic compliance certification is claimed. Provider privacy and retention settings must be checked for the deployment you choose.
- The public repository contains only code, documentation and fictional fixtures. See [SECURITY.md](SECURITY.md).

## Development

```sh
uv run --no-editable ruff check .
uv run --no-editable pytest -q
python3 scripts/check_public.py --history
```

The tests cover generic routing and tool schemas, shared scenario IDs across projects, multiple languages, independent segments, adapter extensions, one/five-model reports, budget controls, privacy guards and error handling. All tests run offline.
