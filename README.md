# 测试陪练 Agent（data-ask-assistant）

面向嵌入式 IPC（网络摄像头）测试同学的本地 AI 陪练工具。基于 Gradio + LLM + SQLite FTS5 用例检索，帮助测试人员**训练问题定位能力**、**速查用例**、**出题自测**，并可通过外部执行框架**真机执行用例并比对结果**。

## 快速启动（每次使用前）

打开 PowerShell，执行：

```powershell
cd d:\TraeCN\data-ask-assistant\data-ask-assistant
python app.py
```

看到日志输出 `Running on local URL: http://127.0.0.1:7860` 后，浏览器访问：

**http://127.0.0.1:7860**

- 服务启动后终端窗口不能关闭，关闭即停止服务
- 停止服务：终端里按 `Ctrl + C`
- 若提示端口被占用，说明已有实例在跑，直接访问网址即可

## 核心功能

### 四种模式（页面顶部 Radio 切换）

| 模式 | 说明 | 提示词 |
|---|---|---|
| **陪练模式**（默认） | 六步定位法引导式陪练：澄清现象 → 确认可复现 → 缩小范围 → 提出假设 → 设计验证 → 复盘归档。每轮只推进一步，不直接给答案；答错给提示再想一次，连续两次错降级为讲解 | `prompts/practice.md` |
| **速查模式** | 直接给结论 + 操作步骤 + 关联用例 | `prompts/quickref.md` |
| **出题模式** | 基于用例库出测试题，作答后按覆盖度/深度/边界/可执行性/原创性五维评分 | `prompts/quiz.md` |
| **执行模式** | 调用 ipc-panel-case-builder 框架真机执行用例：白名单检查 → 确认门 → 执行 → 结果比对 → 不一致自动转陪练分析 | `prompts/execute.md` |

### 用例库动态检索（BM25 双路召回）

- 9000+ 条真实 IPC 测试用例（`cases/cases_clean.json` 导入 SQLite FTS5）
- 三路召回 + 加权重排：FTS5 BM25（×1.0）+ 目录 LIKE 匹配（×1.5）+ 优先级加分（×0.3）
- jieba 中文分词 + 26 条 IPC 领域同义词扩展（如 ota→升级/固件升级/firmware，sdcard→sd卡/tf卡/内存卡）
- TopK 结果 Lazy 截断注入系统提示词，控制 token 预算
- 向量召回层已实现但默认关闭（实测 BM25 纯检索 99ms，质量更优）

### 提示词瘦身架构（500 token 骨架 + 按需注入）

系统提示词只保留「这轮对话必须知道的最短规则」，长规则全部外置到 `prompts/details/`：

| 模式 | 骨架 token | 预算 | 说明 |
|---|---|---|---|
| 陪练 | 265 | ≤500 | 六步名 + 引导规则各一行 |
| 速查 | 102 | ≤300 | 直接作答 + 引用用例 |
| 出题 | 125 | ≤300 | 出题 → 等作答 → 点评 |
| 执行 | 154 | ≤300 | 确认门 + 单人单条 |

每轮注入 = 骨架 + 命中详情 + 模块索引 + TopK + **最近 5 轮历史**（更早不进上下文）。

- **详情按关键词触发**：命中「六步 / 教我 / 怎么定位」才拼 `details/six_step.md`；命中模块词（配网 / OTA / 夜视 …）才拼 `details/ipc_modules.md`；`出题` 命中「评分」才拼评分维度。每轮最多 1 个，单文件 ≤500 token
- **详情与索引互斥**：命中详情时省略模块索引（详情自带上下文，索引只为导航），保证「骨架+详情」最坏情况 ≈765 token 仍不越 800
- **模块索引紧凑化**：21 个模块从「- 名称（N 条用例）」多行改为一行斜杠分隔，370 → 210 token
- **验收工具**：`python tools/prompt_audit.py` 打印每轮实际 prompt 与 token 分解；`python tools/slim_verify.py` 跑骨架预算 + 历史窗口 + 真实 5 轮行为

实测（system prompt 不含 TopK）：

| 模式 | 瘦身前 | 瘦身后 | 降幅 |
|---|---|---|---|
| 陪练·常态轮 | 1247 | 476 | -61.8% |
| 速查·常态轮 | 639 | 313 | -51.0% |
| 出题·常态轮 | 1111 | 335 | -69.8% |
| 执行·常态轮 | 880 | 365 | -58.5% |
| 陪练·命中六步详情 | 1247 | 772 | -38.1% |

### 成长档案（SQLite）

每轮对话自动埋点入库（`db/agent.db`）：

- `sessions` / `messages`：模式、token 用量、延迟、引用用例 ID、回复类型（引导反问/直接作答/评分反馈）
- `quiz_answers`：出题模式五维评分明细
- `week_stats` VIEW：按周统计用例引用率、陪练反问占比、速查占比、平均延迟、token 消耗

## 项目结构

```
├── app.py                  # Gradio 入口（页面 + 会话生命周期）
├── chat_handler.py         # 业务主循环：模式匹配 → 检索注入 → LLM 调用 → 归档
├── llm.py                  # LLM 封装（OpenAI SDK 兼容接口）
├── config.py               # 配置加载器
├── config.yaml             # 本地配置（含密钥，不进 git）
├── requirements.txt
├── prompts/                # 四模式精简骨架（每个 ≤500 token）
│   └── details/            #   外部详情，命中关键词才注入
│       ├── six_step.md     #     六步法完整版 + 引导细则
│       ├── ipc_modules.md  #     各模块 IPC 知识背景
│       ├── practice_examples.md  # 陪练示范对话
│       ├── quiz_rubric.md  #     出题五维评分标准
│       └── execute_flow.md #     执行流程与比对细节
├── retrieval/              # 用例检索链路
│   ├── db.py               #   建表 + 同义词表 + 模块索引（含紧凑版）
│   ├── tokenizer.py        #   jieba 分词 + 同义词扩展 + FTS 查询构建
│   ├── retriever.py        #   三路召回 + 加权重排
│   ├── injector.py         #   目录路由裁剪 + token 预算注入
│   └── vector.py           #   向量召回（已关闭）
├── db/                     # 成长档案
│   ├── schema.sql          #   sessions / messages / quiz_answers / week_stats
│   └── db.py               #   连接 + 写入 + 周统计查询
├── tools/                  # 执行框架适配层 + 验收脚本
│   ├── registry.py         #   单例注册：adapter 切换 + 白名单 + 确认门 + case_mapping
│   ├── cli_adapter.py      #   IPCPanelCheckAnchors（离线门禁）+ IPCPanelRunCase（真机）
│   ├── base.py             #   ToolBase 抽象 + compare() 统一比对
│   ├── tool_types.py       #   ToolResult / ExpectedPlan / CompareResult
│   ├── prompt_audit.py     #   prompt token 审计 + 瘦身前后对比
│   ├── slim_verify.py      #   骨架预算 / 历史窗口 / 5 轮行为验收
│   └── _baseline_prompts/  #   瘦身前提示词快照（供对比）
└── cases/                  # 用例数据
    ├── cases_clean.json    #   清洗后的 9000+ 条用例
    ├── cases_index.md      #   用例库目录索引
    └── clean_cases.py / integrate_cases.py   # 数据清洗与导入脚本
```

## 环境要求

- Python 3.10+（开发验证于 3.13）
- Windows / macOS / Linux
- 可访问的公司内网 LLM 网关（OpenAI 兼容接口）

## 配置步骤

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

> 依赖：`gradio` `openai` `pyyaml` `jieba`

### 2. 创建 config.yaml（不进 git）

在项目根目录新建 `config.yaml`，参考模板：

```yaml
llm:
  base_url: "https://<your-llm-gateway>/v1"
  api_key: "<你的 API Key>"
  model: "deepseek-v4.1-flash"
  timeout: 60

retrieval:
  db_path: "cases/cases.db"
  topk_fts: 30
  topk_catalog: 15
  topk_final: 10
  step_cutoff: 50
  weights:
    bm25: 1.0
    catalog: 1.5
    priority: 0.3
  priority_score:
    P0: 3
    P1: 2
    P2: 1
  min_score: 0.1

# 执行模式可选：ipc-panel-case-builder 框架目录
tool:
  skill_root: "D:/path/to/autotest-ipc-smoke-build"
  check_anchors_timeout: 60000
  run_case_timeout: 600000
  whitelist:
    allowed_case_ids: []
    forbidden_priorities: ["P0"]   # P0 用例禁止自动执行
```

### 3. 初始化用例库（首次运行自动完成）

```bash
python cases/integrate_cases.py    # 导入 cases_clean.json → cases/cases.db（FTS 索引）
```

或直接启动应用，`retrieval.db.init_db()` 会自动建表并装载同义词表。

### 4. 启动

```bash
python app.py
```

浏览器自动打开 `http://127.0.0.1:7860`，选择模式后即可对话。

## 使用样例

### 陪练模式（默认）

```
你：IPC 升级固件后偶尔连不上 WiFi，大概十分之一的概率
Agent：先补齐现场信息——固件版本号从哪升到哪？
      路由器型号和信道？掉线时设备指示灯状态？（只反问，不分析）
...逐轮推进六步定位法，结论留给你自己说出
```

### 速查模式

```
你：速查 SD卡录像回看的用例
Agent：结论 + 关键检查点 + 关联用例 1536949、1537012 ...
```

### 出题模式

```
你：出题：OTA升级
Agent：给出场景题 → 你作答 → 五维评分（覆盖度/深度/边界/可执行性/原创性）+ 差距点评
```

### 执行模式（需配置 skill_root + 真机）

```
你：执行 1536949
Agent：[确认门] 预期 PASS，关键断言 xxx，确认执行？
你：确认执行
Agent：## 执行报告 · 用例 1536949
      ✅ 预期 PASS → 实际 PASS  ⏱️ 耗时 42130ms
      （不一致时自动转陪练：以真实执行数据为素材开始六步定位）

你：跑门禁体检
Agent：## 离线门禁体检报告  ✅ 硬错误：PASS
```

## 注意事项

- **敏感文件不入库**：`config.yaml`（API Key）、`*.db`（用例库/成长档案）、`cases/case_mapping.json` 已在 `.gitignore` 排除，克隆后需自行创建
- **case_mapping 人工确认**：TMS 数字用例 ID 与 skill 的 `cases/*.json` 映射必须人工确认后才生效，Agent 只生成草稿
- **P0 用例保护**：白名单默认禁止 P0 用例自动执行
- **OpenBLAS 内存报错**：jieba→numpy 在低内存机器偶发 `Memory allocation failed`，设置环境变量 `OPENBLAS_NUM_THREADS=1` 可缓解
