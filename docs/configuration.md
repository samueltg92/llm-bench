# Configuration from scratch

This walkthrough uses one model and one fictional project. Replace the fictional content locally. Real prompts, scenarios, credentials and results must remain outside Git. Commands run from the repository checkout.

## 1. Install and create external folders

```sh
uv sync --no-editable --extra dev --extra report
mkdir -p ../bench-private/config ../bench-private/scenarios ../bench-private/source
cp config/bench.yaml ../bench-private/config/bench.yaml
chmod 700 ../bench-private
```

The `report` extra installs PDF dependencies. Python 3.11+ is required. The budget ledger uses POSIX file locking: macOS/Linux are supported; use WSL on Windows. Hooks are optional for consumers but recommended when developing this repository: `git config core.hooksPath .githooks`.

## 2. Import prompts

Start with a platform-neutral bundle:

```sh
cp tests/fixtures/neutral_graph.json ../bench-private/source/project.json
cp tests/fixtures/neutral_scenario.yaml ../bench-private/scenarios/project.yaml
```

Edit those **external** files in your editor. Put the global prompt in `global_system` and each node prompt in `nodes[].prompt`. Preserve the anonymous project alias in both files. Declare the start node, node IDs/names, allowed transitions and tools. Use `composition: single_node` plus `segment` in the scenario when each prompt is an independent segment.

Long prompts can remain in separate local text files while you construct the JSON with Python:

```python
import json
from pathlib import Path

root = Path("../bench-private/source")
bundle = json.loads((root / "project.json").read_text())
# Create these text files locally with your own content before running this snippet.
bundle["global_system"] = (root / "system.txt").read_text()
bundle["nodes"][0]["prompt"] = (root / "first-node.txt").read_text()
(root / "project.json").write_text(json.dumps(bundle, ensure_ascii=False, indent=2))
```

Then validate/import:

```sh
uv run --no-editable vbench import-bundle \
  --input ../bench-private/source/project.json --out ../bench-private/bundles
```

Importing is local; prompts are sent to the configured provider only during an online test. Re-import after editing the source. The bundle gets the original source's hash, allowing reports to distinguish changed prompts. This does not automatically infer tools or business rules from prompt prose.

The older `extract` command accepts its specific graph-export layout. Use neutral JSON for another platform; the expected schema is illustrated in the fixtures and defined in `bundle.py`.

## 3. Define scenarios and function calls

In the external scenario YAML, set:

| Setting | What you supply |
| --- | --- |
| `turns[].content` | Each simulated user question/message. |
| `content_by_node` | Optional alternative user wording for the actual active node. |
| `expect_tools` | Expected native function name, argument subset and min/max call counts for that turn. |
| `tool_mocks` | Simulated results, optional state updates, delays and errors. |
| `tool_prerequisites` | State conditions that must hold before a tool is accepted. |
| `expected_node` / `expected_path` | End-of-turn node and ordered conversation milestones. These check the observed route, never force it. |
| `rules` | Explicit required/prohibited patterns, word limit or tool-before-text checks. |
| `forbidden_tools`, `tool_limits_per_turn` | Calls that must not happen or maximum calls in a turn. |
| `terminal_nodes`, `terminal_tools` | Explicit simulated stopping conditions. |
| `variables` | Values substituted into `{{variable}}` placeholders. |

Each tool needs a name, description and JSON Schema in the bundle. For a configurable destination call, set `routing.tool_name` and `routing.argument_name`; the active node's `transitions` lists allowed aliases/destinations. For a fixed transition after a successful tool result, use `tool_transitions` on that node. Do not connect real backend handlers: the benchmark measures the LLM's attempt and the declared simulation, not backend performance.

For output-language checking, set bundle `allowed_languages: [en]`, `[es]`, another supported ISO language, multiple allowed languages, or `[]` to disable the detector. This is separate from `orchestration_language`, which selects English/Spanish transport labels.

## 4. Configure the model and prices

Create `../bench-private/config/models.yaml`:

```yaml
models:
  model-a:
    display_name: My first model
    enabled: true
    provider: openai_compat
    base_url: https://YOUR_ENDPOINT/v1
    model_id: YOUR_MODEL_ID
    api_key_env: MODEL_A_API_KEY
    context_window: 32000
    supports_system: true
    supports_tools: true
    supports_temperature: true
    output_parameter: max_tokens
    extra: {}
    last_verified: PENDING
```

Replace the endpoint, model ID and context window with verified values for your deployment. Use `max_completion_tokens` when the Chat Completions endpoint requires it. Disable unsupported temperature/system/tool settings. Add effort settings only in the provider's supported format. Extra parameters cannot override the benchmark's output limit, model selection, candidate count or tool orchestration.

For Google, use `provider: google_genai` and the appropriate model ID/environment variable. For a different protocol, implement/install a provider adapter as described in [Extending](extending.md). Adding another catalog key is how you compare another model, endpoint or reasoning profile.

Create `../bench-private/config/pricing.yaml`:

```yaml
models:
  model-a:
    input: null
    output: null
    cached_input: null
    last_verified: PENDING
    source_url: YOUR_PROVIDER_PRICING_PAGE
```

Replace `input` and `output` with verified **USD per million tokens**, and `last_verified` with the verification date. Keep an unknown cache rate null; do not assume a discount. A flat table does not discover volume/context pricing tiers, taxes or extra platform fees. Configure a conservative applicable rate before using the budget bound. Unknown input/output pricing blocks online inference.

Create `../bench-private/.env` locally:

```dotenv
MODEL_A_API_KEY=
```

Enter the real value only in that external file. Never paste it into the README, source code or Git. Set its local permissions with `chmod 600 ../bench-private/.env`. The variable name must match `api_key_env`. Review the selected provider's treatment of your prompts before online tests.

## 5. Set your budget

Create the ledger once:

```sh
uv run --no-editable vbench budget-init \
  --budget-file ../bench-private/budget.json \
  --total 12 --model-limit model-a=8 --per-operation 4
```

This authorizes at most USD 12 in total, USD 8 for `model-a` and USD 4 per operation. The other USD 4 is not automatically assigned to a model. To define multiple models when creating a **new** budget, repeat the option:

```sh
# Alternative initialization for a NEW budget, not a command to reset the one above:
uv run --no-editable vbench budget-init \
  --budget-file ../another-bench-private/budget.json \
  --total 12 --model-limit model-a=8 --model-limit model-b=8 --per-operation 4
```

The total cap still prevents spending USD 16 across the two models. Both global and per-model checks apply. Initialization refuses to overwrite an existing ledger; it has no reset/force flag. Limits support up to six decimal places. Inspect limits and retained capacity at any time:

```sh
uv run --no-editable vbench budget-status --budget-file ../bench-private/budget.json
```

`reserved_usd` is conservative retained application capacity, not confirmed invoice spending. The ledger persists across restarts and is shared by concurrent workers. Never reset it or use another file to evade the same authorized budget. Provider-level spend controls remain useful because this application cannot cap activity outside its runner or charges that its pricing model does not represent.

## 6. Plan, then run a small test

For an initial controlled run, set the external `bench.yaml` to one repetition, one worker, no warmup and one attempt per request (`retries.max_attempts: 1`). Retries and warmups consume budget when enabled. `cost_guard_usd` controls an interactive confirmation threshold; it is **not** the persistent spending ceiling.

```sh
uv run --no-editable vbench doctor \
  --config-dir ../bench-private/config --env-file ../bench-private/.env
uv run --no-editable vbench run --data-dir ../bench-private \
  --config-dir ../bench-private/config --models model-a \
  --repetitions 1 --concurrency 1 --no-warmup --dry-run
uv run --no-editable vbench run --data-dir ../bench-private \
  --config-dir ../bench-private/config --env-file ../bench-private/.env \
  --budget-file ../bench-private/budget.json --models model-a \
  --repetitions 1 --concurrency 1 --no-warmup
```

`doctor` without `--online` and `--dry-run` do not call a model. The dry run shows a conservative reservation based on context, output allowance, turns, tool rounds and retries. If it exceeds a limit, select fewer scenarios/projects, use the individually reserved suite runner, or reconsider the planned test within your authorized budget. Do not truncate real prompts silently or delete reservations to make a test fit. `--yes` skips an interactive confirmation only; it never bypasses budget checks.

A context or quota rejection is different from a model failing a quality assertion. Function names/schemas must be configured; mentioning a tool in plain response text is not a successful native call. Inspect logs before repeating failed tests.

## 7. Read results and compare

```sh
uv run --no-editable python scripts/create_onepager.py \
  --data-dir ../bench-private --config-dir ../bench-private/config \
  --run-root ../bench-private/results --models model-a \
  --budget-file ../bench-private/budget.json --out ../bench-private/deliverables
```

The selected run/source/scenario fingerprints must match. Explicit exclusions/replacements handle diagnostics and follow-ups; mixed profiles are not silently merged. For multiple profiles, prefer different model catalog keys.

- Open `../bench-private/conversation-review/Conversaciones.html` locally. Use **All projects**, **All simulated scenarios**, or a specific conversation.
- `Benchmark-LLM.pdf` gives the compact project comparison; large matrices paginate.
- `Model-consolidated.pdf` and `.csv` show mean, min, max and sample count per metric/model.
- `Detailed-results.csv`, coverage files and original JSON/Markdown histories preserve detail.
- Use the [metric definitions in the README](../README.md#what-the-metrics-mean) and the HTML's definitions to understand timing, native tools, rules, flow, language, tokens and costs.

All these files remain private. PDF/CSV output is anonymized, but anonymous labels alone are not a publication review. Credentials, original prompts, private names and raw conversations do not belong in the public repository.
