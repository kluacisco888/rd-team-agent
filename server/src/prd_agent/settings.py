"""Validated, secret-free runtime configuration."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from prd_agent.contracts import Opus46ModelId, StrictModel, SupportedModelId

__all__ = [
    "AppSettings",
    "DeploymentEnvironment",
    "Opus46ModelId",
    "SupportedModelId",
    "load_settings",
]


# Bedrock knowledge base and data source IDs are 10 alphanumeric characters.
_KNOWLEDGE_BASE_RESOURCE_ID = r"^[0-9a-zA-Z]{10}$"


class DeploymentEnvironment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class AppSettings(StrictModel):
    """Configuration whose values are safe to retain in deployment manifests."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    environment: DeploymentEnvironment
    aws_account_id: str = Field(pattern=r"^\d{12}$")
    aws_region: str = Field(min_length=1)
    model_id: SupportedModelId
    model_max_output_tokens: int = Field(default=65_536, ge=1, le=128_000)
    model_read_timeout_seconds: int = Field(default=600, ge=60, le=900)
    knowledge_base_id: str = Field(pattern=_KNOWLEDGE_BASE_RESOURCE_ID)
    task_data_source_id: str = Field(pattern=_KNOWLEDGE_BASE_RESOURCE_ID)
    project_id: str = Field(min_length=1)
    repository_name: str = Field(min_length=1)
    prd_contract_path: Path
    prompt_version: str = Field(min_length=1)
    runtime_version: str = Field(min_length=1)
    retrieval_top_k: int = Field(default=10, ge=1, le=100)
    retrieval_max_queries: int = Field(default=12, ge=1, le=50)
    allow_global_inference: bool = False

    @field_validator("prd_contract_path")
    @classmethod
    def require_absolute_runtime_paths(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("runtime paths must be absolute")
        return value

    @model_validator(mode="after")
    def guard_global_inference(self) -> Self:
        if self.model_id.startswith("global.") and not self.allow_global_inference:
            raise ValueError(
                "global inference requires explicit ALLOW_GLOBAL_INFERENCE=true"
            )
        return self


_REQUIRED_ENVIRONMENT_KEYS = (
    "DEPLOYMENT_ENV",
    "AWS_ACCOUNT_ID",
    "AWS_REGION",
    "BEDROCK_MODEL_ID",
    "KNOWLEDGE_BASE_ID",
    "TASK_DATA_SOURCE_ID",
    "PROJECT_ID",
    "REPOSITORY_NAME",
    "PRD_CONTRACT_PATH",
    "PROMPT_VERSION",
    "RUNTIME_VERSION",
)


def _parse_bool(value: str | None, *, key: str) -> bool:
    if value is None:
        return False
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ValueError(f"{key} must be true or false")


def _parse_int(value: str | None, *, default: int, key: str) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as error:
        raise ValueError(f"{key} must be an integer") from error


def load_settings(environ: Mapping[str, str]) -> AppSettings:
    """Load only declared, non-secret settings from an injected environment map."""

    missing = [key for key in _REQUIRED_ENVIRONMENT_KEYS if not environ.get(key)]
    if missing:
        raise ValueError(f"missing required settings: {', '.join(sorted(missing))}")

    return AppSettings(
        environment=environ["DEPLOYMENT_ENV"],
        aws_account_id=environ["AWS_ACCOUNT_ID"],
        aws_region=environ["AWS_REGION"],
        model_id=environ["BEDROCK_MODEL_ID"],
        model_max_output_tokens=_parse_int(
            environ.get("BEDROCK_MAX_OUTPUT_TOKENS"),
            default=65_536,
            key="BEDROCK_MAX_OUTPUT_TOKENS",
        ),
        model_read_timeout_seconds=_parse_int(
            environ.get("BEDROCK_READ_TIMEOUT_SECONDS"),
            default=600,
            key="BEDROCK_READ_TIMEOUT_SECONDS",
        ),
        knowledge_base_id=environ["KNOWLEDGE_BASE_ID"],
        task_data_source_id=environ["TASK_DATA_SOURCE_ID"],
        project_id=environ["PROJECT_ID"],
        repository_name=environ["REPOSITORY_NAME"],
        prd_contract_path=Path(environ["PRD_CONTRACT_PATH"]),
        prompt_version=environ["PROMPT_VERSION"],
        runtime_version=environ["RUNTIME_VERSION"],
        retrieval_top_k=_parse_int(
            environ.get("RETRIEVAL_TOP_K"),
            default=10,
            key="RETRIEVAL_TOP_K",
        ),
        retrieval_max_queries=_parse_int(
            environ.get("RETRIEVAL_MAX_QUERIES"),
            default=12,
            key="RETRIEVAL_MAX_QUERIES",
        ),
        allow_global_inference=_parse_bool(
            environ.get("ALLOW_GLOBAL_INFERENCE"),
            key="ALLOW_GLOBAL_INFERENCE",
        ),
    )
