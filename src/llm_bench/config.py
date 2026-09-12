from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    provider: Literal["openai_compat", "google_genai", "fake"]
    model_id: str
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


def yaml_data(path: Path):
    return yaml.safe_load(path.read_text())


def models(path: Path) -> dict[str, Model]:
    return {key: Model.model_validate(value) for key, value in yaml_data(path)["models"].items()}
