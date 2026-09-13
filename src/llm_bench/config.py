from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    provider: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    model_id: str
    display_name: str = ""
    api_key_env: str = ""
    base_url: str | None = None
    context_window: int = Field(default=128000, gt=0)
    supports_system: bool = True
    supports_tools: bool = True
    supports_temperature: bool = True
    include_usage: bool = True
    output_parameter: str = "max_tokens"
    extra: dict = Field(default_factory=dict)
    source_url: str = ""
    last_verified: str = "PENDING"
    notes: str = ""
    data_policy: str = "account_terms"

    @model_validator(mode="after")
    def protect_request_bounds(self):
        if self.provider not in {"openai_compat", "google_genai"}:
            return self
        if self.provider == "openai_compat" and self.output_parameter not in {
            "max_tokens", "max_completion_tokens",
        }:
            raise ValueError("Unsupported output limit parameter for the Chat Completions adapter")
        protected = {
            "model", "messages", "contents", "tools", "stream", "stream_options", "n",
            "candidate_count", "max_tokens", "max_completion_tokens", "max_output_tokens",
            "max_new_tokens", "automatic_function_calling", "system_instruction",
        }
        bodies = [self.extra]
        if isinstance(self.extra.get("extra_body"), dict):
            bodies.append(self.extra["extra_body"])
        if any(protected.intersection(body) for body in bodies):
            raise ValueError("Extra parameters cannot override budgeted output, candidates or orchestration")
        return self


def yaml_data(path: Path):
    return yaml.safe_load(path.read_text())


def models(path: Path) -> dict[str, Model]:
    return {key: Model.model_validate(value) for key, value in yaml_data(path)["models"].items()}


def deployment_signature(record: dict) -> str:
    from .privacy import fingerprint

    return fingerprint({key: record.get(key) for key in (
        "provider", "model_id", "base_url", "extra", "supports_system", "supports_tools",
        "supports_temperature", "include_usage", "output_parameter", "context_window",
    )})


def profile_description(extra: dict) -> str:
    """Describe recorded request settings without guessing model defaults."""
    body = extra.get("extra_body", {})
    body = body if isinstance(body, dict) else {}
    thinking = extra.get("thinking", body.get("thinking", {}))
    reasoning = extra.get("reasoning", body.get("reasoning", {}))
    reasoning = reasoning if isinstance(reasoning, dict) else {}
    parts = []
    if isinstance(thinking, dict) and thinking.get("type"):
        parts.append("Thinking " + str(thinking["type"]))
    elif thinking:
        parts.append("Thinking setting " + str(thinking))
    effort = extra.get("reasoning_effort", body.get("reasoning_effort", reasoning.get("effort")))
    if effort is not None:
        parts.append("effort " + str(effort))
    elif thinking:
        parts.append("effort default")
    google = extra.get("thinking_config", {})
    google = google if isinstance(google, dict) else {}
    if "thinking_level" in google:
        parts.append("Thinking level " + str(google["thinking_level"]))
    if "thinking_budget" in google:
        parts.append("Thinking budget " + str(google["thinking_budget"]))
    return "; ".join(parts) or (
        "No recognized reasoning setting; inspect manifest" if extra else
        "Default profile (no extra request settings)"
    )
