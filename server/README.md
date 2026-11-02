# Strands AgentCore PRD Agent

An English-first Product Requirements Document generator built with the Strands Agents SDK, Anthropic Claude Opus 4.6 on Amazon Bedrock, and Amazon Bedrock AgentCore.

## What it does

- Normalizes product requirements into typed intake contracts.
- Retrieves product and repository evidence exclusively through an AgentCore Managed Knowledge Base exposed by an AWS_IAM-authenticated AgentCore Gateway.
- Generates an English PRD with Strands and `us.anthropic.claude-opus-4-6-v1`.
- Renders Markdown deterministically against a versioned PRD Contract.
- Fails closed on missing sections, structural drift, invalid citations, stale source snapshots, or unknown code paths and symbols.
- Streams `started`, periodic `progress`, final `result`, and sanitized `error` SSE envelopes from AgentCore Runtime.

## Architecture

```text
Client
  -> AgentCore Runtime
     -> Strands requirement analyzer
     -> AgentCore Gateway (AWS_IAM / SigV4)
        -> AgentCore Managed Knowledge Base
     -> Strands PRD composer (Claude Opus 4.6)
     -> deterministic Contract validation and bounded repair
     -> SSE result + validation report
```

AgentCore Managed Knowledge Base is the sole retrieval layer. Source code is ingested through a deterministic manifest and immutable snapshot so generated code impacts cannot refer to stale or unknown repository content.

## Project structure

- `app.py` — AgentCore Runtime entrypoint.
- `src/prd_agent/` — contracts, workflow, Strands adapters, retrieval, validation, rendering, authentication, and deployment configuration.
- `contracts/` — versioned machine-readable PRD Contract.
- `examples/` — non-authoritative mock PRD fixture.
- `scripts/` — account-locked isolated test-stack provisioner and Gateway smoke tools.
- `tests/` — unit and regression tests.
- `deploy/evidence/cloud-e2e-007/` — saved output and validation evidence from the successful mock cloud E2E run.

## Local validation

Python 3.12 is required. Dependencies are pinned exactly.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py' -q
```

Current result: **136 tests passing**.

## Regional one-click deployment

[`AGENT_DEPLOYMENT_RUNBOOK.md`](AGENT_DEPLOYMENT_RUNBOOK.md) is an AI-agent-executable runbook for deploying the full mock stack (S3, Managed Knowledge Base, Gateway, Runtime, IAM) into any supported Region and generating one PRD end to end:

```bash
scripts/one_click_deploy.sh --profile <profile> --region <region> --expected-account <account>          # read-only dry-run
scripts/one_click_deploy.sh --profile <profile> --region <region> --expected-account <account> --apply  # deploy + E2E
```

## Cloud E2E result

The isolated mock deployment completed a genuine end-to-end invocation in AWS account `034362076319`, `us-east-1`:

- HTTP 200 with `text/event-stream`.
- Final event: `result`; business status: `completed`.
- 24/24 required PRD sections and exact H3/table signatures verified.
- Validation report passed with no remaining issues.
- Citations were restricted to chunks returned by the Managed Knowledge Base.
- Runtime, prompt, Contract, ingestion, repository snapshot, and model versions were pinned in the result.

See [`deploy/evidence/cloud-e2e-007/README.md`](deploy/evidence/cloud-e2e-007/README.md) for the evidence summary and hashes. The fixture and generated PRD are explicitly mock and non-authoritative.

## Deployment safety

The provisioner is account-locked and uses isolated `prd-agent-mock-e2e` names. Review its dry-run before applying it to an AWS account. It does not contain resource deletion operations, and no cleanup should be performed without explicit authorization.

AWS credentials are never stored in this repository. Configure an AWS CLI profile externally and let the CLI/SDK resolve it through the normal credential chain.

## License

No license has been granted. All rights are reserved unless the repository owner adds a license.
