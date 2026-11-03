"""Non-secret orchestrator configuration loaded from the Runtime environment."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from pydantic import ConfigDict, Field, field_validator

from prd_agent.contracts import StrictModel, SupportedModelId

_RESOURCE_ID = r"^[A-Za-z0-9]{10}$"


class OrchestratorSettings(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    aws_region: str = Field(min_length=1)
    aws_account_id: str = Field(pattern=r"^\d{12}$")
    model_id: SupportedModelId
    orchestrator_max_output_tokens: int = Field(default=8_192, ge=1, le=128_000)
    prd_max_output_tokens: int = Field(default=65_536, ge=1, le=128_000)
    model_read_timeout_seconds: int = Field(default=600, ge=60, le=900)
    knowledge_base_id: str = Field(pattern=_RESOURCE_ID)
    task_data_source_id: str = Field(pattern=_RESOURCE_ID)
    project_id: str = Field(min_length=1)
    repository_name: str = Field(min_length=1)
    repository_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    prd_contract_version: str = Field(min_length=1)
    prd_contract_path: Path
    source_manifest_root: Path
    memory_id: str = Field(min_length=1)
    preference_strategy_id: str = Field(min_length=1)
    semantic_strategy_id: str = Field(min_length=1)
    summary_strategy_id: str = Field(min_length=1)
    results_bucket: str = Field(min_length=3)
    cognito_issuer: str = Field(
        pattern=r"^https://cognito-idp\.[a-z0-9-]+\.amazonaws\.com/[\w-]+$"
    )
    allowed_client_ids: tuple[str, ...] = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    runtime_version: str = Field(min_length=1)
    history_turns: int = Field(default=20, ge=1, le=100)

    @field_validator("prd_contract_path", "source_manifest_root")
    @classmethod
    def require_absolute(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("runtime paths must be absolute")
        return value


_ENV = {
    "aws_region": "AWS_REGION",
    "aws_account_id": "AWS_ACCOUNT_ID",
    "model_id": "BEDROCK_MODEL_ID",
    "knowledge_base_id": "KNOWLEDGE_BASE_ID",
    "task_data_source_id": "TASK_DATA_SOURCE_ID",
    "project_id": "PROJECT_ID",
    "repository_name": "REPOSITORY_NAME",
    "repository_commit": "REPOSITORY_COMMIT",
    "prd_contract_version": "PRD_CONTRACT_VERSION",
    "prd_contract_path": "PRD_CONTRACT_PATH",
    "source_manifest_root": "SOURCE_MANIFEST_ROOT",
    "memory_id": "MEMORY_ID",
    "preference_strategy_id": "MEMORY_PREFERENCE_STRATEGY_ID",
    "semantic_strategy_id": "MEMORY_SEMANTIC_STRATEGY_ID",
    "summary_strategy_id": "MEMORY_SUMMARY_STRATEGY_ID",
    "results_bucket": "RESULTS_BUCKET",
    "cognito_issuer": "COGNITO_ISSUER",
    "allowed_client_ids": "ALLOWED_CLIENT_IDS",
    "prompt_version": "PROMPT_VERSION",
    "runtime_version": "RUNTIME_VERSION",
}
REQUIRED_ENVIRONMENT_KEYS = tuple(_ENV.values())


def load_orchestrator_settings(environ: Mapping[str, str]) -> OrchestratorSettings:
    """Fail closed on any missing key; secrets are never configured here."""

    missing = sorted(key for key in REQUIRED_ENVIRONMENT_KEYS if not environ.get(key))
    if missing:
        raise ValueError(f"missing required settings: {', '.join(missing)}")
    values: dict[str, object] = {field: environ[key] for field, key in _ENV.items()}
    values["allowed_client_ids"] = tuple(
        item.strip()
        for item in environ["ALLOWED_CLIENT_IDS"].split(",")
        if item.strip()
    )
    values["prd_contract_path"] = Path(environ["PRD_CONTRACT_PATH"])
    values["source_manifest_root"] = Path(environ["SOURCE_MANIFEST_ROOT"])
    return OrchestratorSettings.model_validate(values)
