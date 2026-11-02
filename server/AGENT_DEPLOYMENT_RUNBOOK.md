# AgentCore PRD Agent：AI 一键部署 Runbook

> 读者：替客户执行部署的 AI 代理，以及审批它的人。
> 严格按章节顺序执行，不跳步、不自行放宽检查。所有命令都在仓库根目录运行。

## 0. 目标与成功标准

在目标 AWS 账号和 Region 中部署一套隔离的 mock 环境，并用一份 mock 需求生成一份英文 PRD：

| 组件 | 资源 |
|---|---|
| 知识源存储 | S3 bucket `prd-agent-mock-e2e-<account>-<region>`（私有、加密、版本化） |
| 唯一检索层 | Amazon Bedrock Managed Knowledge Base + S3 data source + 一次摄取任务 |
| 检索网关 | AgentCore Gateway（AWS_IAM / SigV4）+ Managed KB connector target |
| Agent 运行时 | AgentCore Runtime `prd_agent_mock_e2e_direct`（direct code deployment，Python 3.12，arm64） |
| 权限 | 3 个 IAM role：`<prefix>-<region>-kb` / `-gateway` / `-runtime`（最小权限） |
| 模型 | Claude Opus 4.6（默认，已有 E2E 证据）或 Claude Opus 5.5（`--model-family opus-5-5`），按 Region 自动选择 Geo/Global 推理配置 |

**只有满足以下全部条件，才算部署成功**（Runtime 状态为 `READY` 本身不算）：

1. `scripts/one_click_deploy.sh --apply` 退出码为 `0`，最后输出 `E2E PASS`。
2. `deploy/out/<region>/e2e-latest.json` 中 `final_event = "result"`、`status = "completed"`、`validation_passed = true`。
3. `deploy/out/<region>/runs/<run_id>/prd.md` 存在，文件开头标明 MOCK / non-authoritative。

## 1. 执行规则（AI 必须遵守）

1. **先 dry-run，后 apply。** dry-run 不创建任何资源。只有人类明确批准 dry-run 输出后，才能加 `--apply`。
2. **不接触密钥。** 只使用 `--profile`；不得打印、记录或写入 AK/SK/Session Token，也不得把它们写进 `.env` 或提交到 Git。
3. **失败即停。** 任何一步失败就停止，并报告步骤号、错误信息和日志文件路径。不得为了让检查通过而修改参数。
4. **以下开关只能由人类授权：**
   - `--allow-global-inference`：需要确认数据驻留规则允许全球推理后才能使用。
   - `--allow-unlisted-region`：需要确认 AWS 已在该 Region 提供 Managed KB 后才能使用。
5. **不删除资源。** 只有人类明确要求清理时，才执行第 12 节。
6. **不修改 `src/`。** 源码快照哈希会写入知识库元数据。如果确实修改了，必须重新执行 `--apply`（脚本会检测到不一致并拒绝部署）。

## 2. 需要人类提供的输入

| 变量 | 含义 | 示例 |
|---|---|---|
| `PROFILE` | 已配置好的 AWS CLI profile | `agentcore-deploy` |
| `REGION` | 目标 Region，必须在第 3 节支持列表中 | `ap-southeast-2` |
| `ACCOUNT` | 12 位账号 ID，每一步都会核对 | `123456789012` |
| `ENVIRONMENT` | `development` / `test` / `production`，默认 `development` | `development` |
| `MODEL_FAMILY` | `opus-4-6`（默认）或 `opus-5-5` | `opus-5-5` |
| 是否允许 Global 推理 | 仅在所选模型在该 Region 没有 Geo 推理配置时需要 | 是 / 否 |

获取账号 ID（只读）：

```bash
aws sts get-caller-identity --profile "$PROFILE" --query Account --output text
```

## 3. Region 支持矩阵

依据 AWS 官方文档（2026-10-16 核对）。约束来自 Managed Knowledge Base 的可用 Region；AgentCore Runtime 和 Gateway 的可用 Region 范围更大。

| Region | Managed KB | `opus-4-6` 自动选择 | `opus-5-5` 自动选择 |
|---|---|---|---|
| `us-east-1` | ✓ | `us.anthropic.claude-opus-4-6-v1` | `us.anthropic.claude-opus-5-5` |
| `us-west-2` | ✓ | `us.anthropic.claude-opus-4-6-v1` | `us.anthropic.claude-opus-5-5` |
| `eu-west-1` | ✓ | `eu.anthropic.claude-opus-4-6-v1` | `eu.anthropic.claude-opus-5-5` |
| `eu-west-2` | ✓ | `eu.anthropic.claude-opus-4-6-v1` | `eu.anthropic.claude-opus-5-5` |
| `eu-central-1` | ✓ | `eu.anthropic.claude-opus-4-6-v1` | `eu.anthropic.claude-opus-5-5` |
| `ap-southeast-2`（悉尼） | ✓ | `au.anthropic.claude-opus-4-6-v1`（数据留在澳洲） | `au.anthropic.claude-opus-5-5`（数据留在澳洲） |
| `ap-northeast-1`（东京） | ✓ | `global.anthropic.claude-opus-4-6-v1`（**需要** `--allow-global-inference`） | `jp.anthropic.claude-opus-5-5`（数据留在日本，不需要 Global） |
| 其他 Region（含 `ap-southeast-1` 新加坡） | ✗ | — | —（preflight 会直接拒绝） |

不在列表中的 Region 不能按现有架构部署，因为 Managed KB 是方案规定的唯一检索层。

## 4. 前置条件

- macOS 或 Linux，有 `bash`、`git`、AWS CLI v2。
- [uv](https://docs.astral.sh/uv/)：缺少 Python 3.12 时会自动下载，并用于构建 linux/arm64 依赖包。本机不需要 Docker。
- 能访问 PyPI 和 AWS API 的网络。
- 调用者 IAM 权限：在沙箱账号中最简单的做法是 `AdministratorAccess`。如果要最小化，需要覆盖以下操作：
  - IAM：在 `/agentcore-prd-agent-test/` 路径下创建、更新角色和内联策略，打标签，并对这 3 个角色执行 `iam:PassRole`。
  - S3：创建和配置上述 bucket，并读写其中的对象。
  - Bedrock：创建、读取知识库和数据源，执行摄取和 `Retrieve`，调用 `GetInferenceProfile`、`InvokeModel` 和 `Converse`。
  - AgentCore：创建、读取、列出和更新 Gateway、Gateway target 和 Agent Runtime；执行 `InvokeGateway`、`InvokeAgentRuntime` 和 `StopRuntimeSession`。
- 如果使用临时凭证，开始前剩余有效期至少 1 小时。凭证中途过期不会损坏环境：刷新凭证后重新执行同一条命令即可，脚本是幂等的。

## 5. 获取代码

两种方式任选一种：

```bash
# A. 收到 zip 包时（离线交付）
unzip agentcore-prd-agent-bundle-*.zip
cd agentcore-prd-agent-bundle/strands-agentcore-prd-agent
chmod +x scripts/*.sh scripts/*.py   # 某些解压工具会丢失可执行位

# B. 从 Git 获取
git clone --branch <交付分支> https://github.com/warren830/strands-agentcore-prd-agent.git
cd strands-agentcore-prd-agent
```

## 6. 一键执行

### 6.1 Dry-run（只读，必须先跑）

```bash
scripts/one_click_deploy.sh --profile "$PROFILE" --region "$REGION" --expected-account "$ACCOUNT" \
  --model-family "$MODEL_FAMILY"
# 所选模型在该 Region 只能走 Global 推理时（如 opus-4-6 + 东京），人类授权后追加：--allow-global-inference
```

dry-run 会：

- 安装依赖；
- 运行全部单元测试；
- 运行 preflight，并发起一次极小的模型调用（最多 32 tokens）来确认模型访问权限（费用极低；加 `--skip-model-probe` 可跳过）；
- 输出资源计划；
- 在本地构建 Runtime 部署包。

判定 dry-run 通过的标准：

- 最后一行是 `DRY-RUN COMPLETE. No AWS resources were created.`
- `deploy/out/<region>/preflight.json` 中 `"passed": true`。

然后把 `deploy/out/<region>/provision-plan.json` 中的资源名、Region、模型 ID 和 IAM 角色名交给人类审批。

### 6.2 Apply（人类批准后）

```bash
scripts/one_click_deploy.sh --profile "$PROFILE" --region "$REGION" --expected-account "$ACCOUNT" \
  --model-family "$MODEL_FAMILY" --apply
```

预计耗时 25–40 分钟，其中 PRD 生成约 8–15 分钟。执行期间 stderr 会持续打印 `WAIT ...` 或 `EVENT progress ...`，这是正常现象，不要中断。

## 7. 各步骤说明与判定

| 步骤 | 动作 | 写 AWS | 成功判定 | 产物（`deploy/out/<region>/`） |
|---|---|---|---|---|
| 1 | 创建 `.venv`（Python 3.12），按 `requirements.txt` 精确版本安装依赖 | 否 | 无报错 | — |
| 2 | 单元测试 | 否 | 输出 `OK` | — |
| 3 | `scripts/preflight.py`：核对账号、Region、推理配置是否 ACTIVE、API 是否可达、模型能否调用 | 否 | `"passed": true` | `preflight.json` |
| 4 | `scripts/provision_test_stack.py`（dry-run） | 否 | 输出资源计划，`deletion_operations: []` | `provision-plan.json` |
| 5 | `scripts/deploy_runtime.py --build-only`：构建 arm64 zip 包 | 否 | 包小于 250 MB，并输出 `source_snapshot` | `build/runtime/deployment_package.zip` |
| 6 | provision `--apply`：创建 S3、上传 mock 知识，创建 KB 和数据源，完成摄取并做一次检索冒烟，创建 Gateway、target 和 3 个角色 | **是** | `ingestion_status = COMPLETE`，`gateway_status = READY` | `stack-state.json`、`provision-apply.log` |
| 7 | `scripts/smoke_gateway.py`：经 SigV4、Gateway 到 KB 的真实检索 | 否 | 退出码 0，`status = success` | `gateway-smoke.json` |
| 8 | `scripts/deploy_runtime.py --apply`：上传部署包，创建或更新 Runtime，等待 `READY` | **是** | 状态文件中出现 `runtime_arn`，且 `runtime_status = READY` | `runtime-deploy.log` |
| 9 | `scripts/invoke_runtime.py`：用 mock 需求调用 Runtime，保存 SSE 和 PRD | 否（仅调用） | 退出码 0（2 表示需要澄清，1 表示失败） | `e2e-latest.json`、`runs/<run_id>/` |

## 8. Mock 数据说明

本流程使用仓库自带的 mock 数据跑通。这些 mock 数据都不是权威业务内容。

| 用途 | 文件 | 说明 |
|---|---|---|
| PRD Contract（PRD 标准） | `contracts/mock-csv-batch-user-import.contract.json` | 版本 `mock-csv-import-v0.1`，规定 24 个必需章节、H3 和表格签名 |
| 知识库文档 | `examples/mock-csv-batch-user-import-prd.md` | 按标题切块后写入 `content/document/` |
| 代码仓库 | 本仓库的 `src/` | 按符号切块后写入 `content/code/`，快照 ID 为 `source-<sha>` |
| 需求输入 | `deploy/cloud-e2e-request.json` | 一句 CSV 批量导入预览需求，`one_shot` 模式 |

换成真实数据时，需要替换 Contract、文档、代码仓库和需求。这属于方案 P0/P1 的工作，不在本 Runbook 范围内。

## 9. 产物与状态文件

`deploy/out/<region>/` 目录已加入 `.gitignore`，不要提交：

- `stack-state.json`：后续步骤和清理依赖的唯一状态来源，包含 KB、data source、摄取任务、Gateway、Runtime 的 ID 和 ARN，以及源码快照和模型 ID。不含密钥。
- `runs/<run_id>/`：每次 E2E 运行的证据，包括 `request.json`、`response.sse`、`prd.md`、`validation-report.json` 和 `result-metadata.json`。

可以单独再跑一次 E2E，不需要重新部署：

```bash
PYTHONPATH=src .venv/bin/python scripts/invoke_runtime.py --profile "$PROFILE" --region "$REGION" \
  --expected-account "$ACCOUNT" --state-file "deploy/out/$REGION/stack-state.json" \
  --out-dir "deploy/out/$REGION/runs"
```

## 10. 向人类汇报的格式

```text
结果：PASS / FAIL（失败步骤 N）
账号 / Region / 环境：<account> / <region> / <env>
模型：<model_id>（Geo 或 Global）
Runtime ARN：<runtime_arn>
KB ID / 摄取任务：<knowledge_base_id> / <ingestion_job_id>
E2E：status=<status>，validation_passed=<bool>，events=<n>，耗时=<s>s
PRD：deploy/out/<region>/runs/<run_id>/prd.md
失败时：错误原文 + 日志路径 + 建议的下一步
```

## 11. 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| preflight `region.support` 失败 | 该 Region 没有 Managed KB | 改用第 3 节中的 Region |
| `model.inference_profile` 或 `model.invoke` 失败 | 模型未开通、Anthropic 首次使用表单未提交，或被 SCP 拒绝 | 在 Bedrock 控制台完成模型访问申请，或联系账号管理员 |
| `AWS account mismatch` | profile 指向了其他账号 | 修正 `--profile` 或 `--expected-account` |
| `ExpiredToken` / `InvalidClientTokenId` | 临时凭证过期 | 刷新 profile 凭证后重跑同一命令 |
| `AccessDenied ... iam:PassRole` | 调用者缺少权限 | 补齐第 4 节中的权限 |
| 摄取状态 `FAILED` | 数据源或 bucket 权限问题 | 查看 `provision-apply.log` 和 Bedrock 控制台中摄取任务的失败原因 |
| `src/ changed after the knowledge base was ingested` | 部署前改过 `src/` | 重新执行 `--apply` |
| Runtime `CREATE_FAILED` / `UPDATE_FAILED` | 部署包或环境变量问题 | 读取报错中的 `failureReason`；确认部署包是 arm64 构建 |
| E2E 返回 `GENERATION_FAILED` | Runtime 内部异常 | 查看 CloudWatch 日志组 `/aws/bedrock-agentcore/runtimes/<runtime_id>-DEFAULT`，按 `run_id` 过滤 |
| E2E 退出码 2 | 模型要求澄清 | 确认请求中 `mode` 为 `one_shot`，或补充更完整的需求 |
| Opus 4.6 / 5.5 `ThrottlingException` | 配额不足 | 稍后重试，或申请提高配额 |
| Opus 5.5 报 `ValidationException`，提到 `tool_choice` | 模型没有主动调用结构化输出工具，Strands 改用强制工具调用重试，而 Opus 5.5 只支持 `auto`/`none` | 重跑一次；如果反复出现，改回 `--model-family opus-4-6` 并报告 |

## 12. 清理（仅在人类明确要求时）

```bash
# 1) 只读：列出将被删除的资源
PYTHONPATH=src .venv/bin/python scripts/teardown_stack.py --profile "$PROFILE" --region "$REGION" --expected-account "$ACCOUNT"
# 2) 人类确认清单后才执行；不可逆，会删除 bucket 中全部对象版本
PYTHONPATH=src .venv/bin/python scripts/teardown_stack.py --profile "$PROFILE" --region "$REGION" --expected-account "$ACCOUNT" \
  --apply --confirm-prefix prd-agent-mock-e2e
```

清理脚本只删除名称完全匹配、并带有 `Project=AgentCorePrdAgent` 标签的资源。遇到任何不是本流程创建的资源，会直接拒绝删除。

## 13. 已知限制（与 plan1 方案的差距）

- 本 mock 流程验证的是 Runtime、Gateway、Managed KB 和 Claude Opus 4.6 这条主链路。AgentCore Identity、Memory、Evaluations 和 Policy 尚未接入。
- Observability 目前只有 CloudWatch Runtime 日志，尚未接入 ADOT/X-Ray trace。
- 只支持一个 PRD Contract、一个项目、手动摄取。
- Runtime 角色的 foundation-model ARN 不限定 Region，但只允许当前所选的那一个模型。这是因为跨 Region 推理的目标 Region 会随 AWS 扩展而变化。
- Opus 5.5 与 4.6 的行为差异：
  - Opus 5.5 的自适应思考始终开启（默认 effort 为 medium），延迟和 token 用量会和 4.6 不同。
  - 工具调用只支持 `tool_choice` 为 `auto`/`none`，所以 Strands 的强制结构化输出重试在 5.5 上不可用。
  - 已用真实调用验证过：5.5 能主动调用结构化输出工具并通过 Pydantic 校验（需求解析这一步）。完整 PRD 生成需要以 `--apply` 的 E2E 结果为准。
- plan1 方案把模型固定为 Opus 4.6。改用 5.5 属于架构决策变更，需要方案负责人确认。
