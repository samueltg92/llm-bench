# Extending llm-bench

## Project contract

A neutral bundle is a JSON document matching `Bundle` in `src/llm_bench/bundle.py`. Import it with `vbench import-bundle`. The importer hashes the original bytes; the graph does not need a platform-specific `bot`, `functions` or `config` wrapper. Start from `tests/fixtures/neutral_graph.json`.

The anonymous project alias is a storage/privacy convention, not a business-domain restriction. Tool and node names stay in external inputs and private transcripts. Each project may contain different prompts, tools, schemas and scenarios. Two projects may reuse a scenario ID without losing an observation.

A parameterized route uses `routing.tool_name`, `routing.argument_name` and the active node's `transitions`. A fixed transition uses `nodes[].tool_transitions` after a successful simulated business function. Tool arguments still undergo JSON Schema validation and configured prerequisites. Mock failures do not follow fixed transitions. Further calls emitted in the same model response after a transition are rejected until the model has received the new node prompt.

The parameterized route is a generated transport schema with one string destination field. More complex backend-dependent transition decisions need an explicit adapter/engine extension; they are not inferred from prompt prose. A function mentioned in a prompt needs a declared schema or an explicitly marked simulated platform interface. Supplying a mock does not establish that a real backend executed successfully.

`orchestration_language` selects the added English/Spanish transport labels. `allowed_languages` independently controls the output-language detector; an empty list disables it. Original prompts are not translated. `routing.transport_note` can explicitly describe source-interface aliases; the core does not inject platform-specific alias rules.

The legacy `extract` command handles its documented export shape. Re-import or explicitly configure compatibility notes when migrating an old bundle that relied on a transport alias. Changing transport instructions changes the experiment; do not claim old/new requests are identical merely because the original prompt file is unchanged.

## Provider contract

Built-in adapters implement Chat Completions-compatible streaming and Google Gen AI. Model catalog entries select an adapter and deployment parameters; they do not select scoring logic. A new model behind an existing compatible endpoint only needs configuration and verified pricing/capabilities.

For another protocol, publish/install a trusted Python package with this entry point:

```toml
[project.entry-points."llm_bench.providers"]
my_adapter = "my_package.adapter:create_provider"
```

```python
# my_package/adapter.py
from llm_bench.providers.base import StreamEvent, Usage


def create_provider(model, timeouts):
    return MyProvider(model, timeouts)


class MyProvider:
    def __init__(self, model, timeouts):
        self.model = model
        self.timeouts = timeouts

    def stream_chat(self, *, messages, tools, temperature, max_output_tokens):
        # Replace with your SDK transport. Do not make paid calls in __init__.
        # Yield text, reasoning, incremental tool_call, usage and done events.
        raise NotImplementedError("Implement the provider's streaming protocol")
        yield StreamEvent(kind="usage", usage=Usage())

    def close(self):
        pass
```

Set `provider: my_adapter` in the model catalog. Installed entry-point code is trusted executable code; no code is imported from prompts, tool schemas or exports. The CLI reserves budget before calling the transport. A plugin must not launch hidden requests, background generation or its own unaccounted retries.

Events distinguish visible text, separate reasoning, incremental native calls (stable indexes/IDs), provider usage and completion. Normalize cached/reasoning token semantics accurately. The adapter must close its resources. A text-only endpoint can use the explicitly recorded text protocol; emulation cannot pass a native-call expectation. APIs exposing only Responses, proprietary protocols or multimodal outputs need a suitable adapter rather than assuming Chat Completions compatibility.

## Profiles, repetitions and reporting

Use distinct catalog keys when comparing providers, deployments or effort settings. The final report discovers model names from the selected catalog and recorded settings from manifests. It rejects mixed deployments/profiles under one key; display names alone do not define a profile. A suite resume does not reuse observations from a changed provider, endpoint or reasoning configuration.

PDF project matrices paginate as needed; the consolidated PDF creates a page per selected model. HTML column counts follow the selected records. All aggregate calculations use sample-level data; an unobserved model remains visible with missing measurements. Historical summaries remain a separate comparison with explicit sample counts and no claim of causal improvement.

## Scoring is declarative

`Scenario` defines positive/forbidden tools, argument subsets, counts, prerequisites, per-turn limits, node expectations, ordered milestones and four programmed rule types: `required_regex`, `forbidden_regex`, `max_words`, `tool_before_text`. These are not extracted automatically from a prompt. A new semantic scoring requirement requires a new evaluator and tests.

The engine validates impossible paths and contradictory rule configuration before making calls. A model's announced intention is not a native tool call. An observed function attempt is not proof of backend success. Unknown language/cost/context evidence remains unknown, not an automatic pass.

Language review accepts `false_positive`, `confirmed_foreign` and `pending`; `false_positive_spanish` remains a legacy alias for old policies. Decisions are tied to the exact fragment hash and never rewrite the original response.
