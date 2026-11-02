# 研发团队 Agent（Mac 客户端）

研发同学在 Mac 上通过对话使用的 Agent。能力全部来自后台编排 Agent 可调用的工具，PRD 生成是第一个工具。

## Language

### 对话

**会话 (Session)**:
一个用户和编排 Agent 之间的一段多轮对话，可以从该用户的会话列表里重新打开、接着聊。
_Avoid_: 任务、聊天记录、对话框

**编排 Agent (Orchestrator)**:
运行在 Runtime 上、理解用户意图并决定调用哪些工具的那个 Agent。
_Avoid_: 主 Agent、bot、PRD Agent

**工具 (Tool)**:
编排 Agent 可以调用的一项后台能力；给 Agent 加能力就是加一个工具。
_Avoid_: 功能入口、菜单、插件

### PRD 生成

**Runtime**:
部署在 AWS 上、运行编排 Agent 的服务端，用 Runtime ARN 标识。
_Avoid_: HTTP 接口、后端、服务器

**任务 (Task)**:
PRD 生成工具的一次执行，从拿到需求和文档到产出 PRD；它的文档只对它自己可见。
_Avoid_: 作业、job

**任务文档 (Task Document)**:
随一个任务提交的本机 Markdown 或纯文本文件，任务结束后从 Runtime 一侧删除。
_Avoid_: 知识库文档、上传文件

**领域知识库 (Domain Knowledge Base)**:
客户维护的参考资料，PRD 生成工具从中检索依据；资料存放在哪里不改变这个概念。
_Avoid_: SharePoint、KB、文档库

**需求 (Requirement)**:
用户为本次任务写的一段功能描述，PRD 围绕它展开。
_Avoid_: prompt、提示词

**连接设置 (Connection Settings)**:
在 app 里配置、不含密钥的 Runtime 连接信息：Runtime ARN、项目、源码快照和 PRD 模板版本。
_Avoid_: 配置文件、接口地址

**访问密钥 (Access Key)**:
给每个请求签名用的 AWS Access Key ID 和 Secret，可附带 Session Token；不存在"已登录"状态。
_Avoid_: 登录、账号密码

**源码快照 (Source Snapshot)**:
标识 Runtime 部署时打包的那份代码的 ID，任务必须与之一致。
_Avoid_: commit、版本号

**PRD 模板 (PRD Contract)**:
规定 PRD 章节结构的版本化模板，由 Runtime 持有。
_Avoid_: schema、格式

**输出目录 (Output Folder)**:
用户指定的本机目录；每个任务在其中新建一个子目录存放结果。
_Avoid_: 下载目录

**校验报告 (Validation Report)**:
对生成的 PRD 做确定性检查后的结果，随 PRD 一起保存。
_Avoid_: 评估结果
