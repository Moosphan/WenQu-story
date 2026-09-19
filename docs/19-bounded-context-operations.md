# 有界上下文与长期记忆操作手册

以下入口均操作显式指定的本地书库。高级作者操作通过 CLI/HTTP 提供，模型不能自行将提案核实为真。`BOOK`、版本和实体 ID 应从本书查询结果取得；示例中的占位符不是可直接运行的真实 ID。

## 配置与容量

书库 `context-policy.json` 默认为 shadow；只有 adaptive 对可选材料装箱。24K 是软目标，硬依赖、必要历史、输出和请求开销一起受模型容量约束。硬内容放不下时仍明确阻断，不截断正文、不自动扩大预算。

按准确模型名配置本地 tokenizer：

```json
{
  "mode": "adaptive",
  "counting_model": "exact-model-name",
  "model_windows": {"exact-model-name": 128000},
  "tokenizer_profiles": {
    "exact-model-name": {"path": "/absolute/tokenizer.json", "sha256": "64位小写SHA256"}
  }
}
```

容量必须换成已确认值。文件校验失败显式报错；未配置模型保守回退。计数仍包含请求模板不确定性，输出和开销预留不会消失。SQLite 10 不支持旧程序直接降级；回滚应用前恢复配套备份。`off` 只回滚资料选择，不降级数据库。

## 作者记忆操作

命令前缀为 `.venv/bin/python -m story_core.cli --root /absolute/books`。先用 `show BOOK` 获取 revision；所有核实/合并/修复使用该 revision 和唯一 request ID，并可用相同请求安全重试。

|操作|CLI 后缀|HTTP（`/api/books/BOOK` 后缀）|
|---|---|---|
|合并预览|`memory BOOK merge-preview SOURCE TARGET`|POST `/memory/merge/preview`|
|合并提交|`memory BOOK merge SOURCE TARGET --resolutions-file decisions.json --actor author --expected-revision N --request-id ID`|POST `/memory/merge`|
|修复预览|`memory BOOK repair-preview VERSION`|GET `/memory/repairs/VERSION`|
|修复提交|`memory BOOK repair VERSION --file repair.json`|POST `/memory/repairs/VERSION`|
|摘要列表|`memory BOOK summaries`|GET `/memory/summaries`|
|摘要提案|`memory BOOK summary-propose --file summary.json`|POST `/memory/summaries`|
|摘要核实|`memory BOOK summary-decide SUMMARY --decision accept --actor author --expected-revision N --request-id ID --verified-by-author`|POST `/memory/summaries/SUMMARY/decision`|
|伏笔调度|`memory BOOK promise-schedule --file promise.json`|POST `/memory/promises`|
|查看到期伏笔|`memory BOOK promises --chapter 15 --story-time 20`|GET `/memory/promises?chapter_number=15&story_time=20`|
|独立分支|`fork BOOK --name 分支名 --actor author --expected-revision N --request-id ID`|POST `/branches`|
|分支列表|`branches BOOK`|GET `/branches`|

合并预览列出冲突事件。决策文件为预览要求的事件 ID 到胜出事件 ID 映射；不能通过选“更新”事件静默覆盖矛盾。合并保留旧 ID 重定向及来源，不改写正文。

修复文件包含 `actor`、`expected_revision`、`request_id`、`trust:true`、`replacements`、`retire_event_ids`、`dependency_versions`。每条 replacement 为 `{"event_id":"旧事件","candidate":{...}}`，candidate 使用事实登记或伏笔状态事件对应字段。当前来源内的旧事件必须逐项处理；来源已经替换时退休旧事件，并另行对新来源提交事实候选。仅修复记忆不自动批准需要复审的章节。

摘要提案格式：

```json
{
  "level": "chapter", "chapter_start": 1, "chapter_end": 1,
  "text": "作者或模型提供、尚待核实的摘要。",
  "source_refs": [{"chapter_number": 1, "version_id": "VERSION", "quote": "对应的原文引用"}],
  "visibility": "author", "origin": "author", "request_id": "summary-proposal-1"
}
```

chapter 最多 2,000 字符，volume 最多 4,000 字符、覆盖至多 50 章，每章均须当前定稿来源。匹配引文只验证出处；核实含义需要单独 accept + trust。原文或传递依赖失效后，摘要停止参与输入。系统不会自动调用模型生成摘要。

伏笔 CLI 文件示例：

```json
{
  "label": "归还信物", "actor": "author", "expected_revision": 4,
  "request_id": "promise-1", "due_from_chapter": 15, "due_to_chapter": 18,
  "relation_triggers": [{"fact_id": "FACT", "equals": "已到达"}], "mandatory": true
}
```

HTTP 将调度字段放进 `options`，身份与 revision 字段仍在顶层。区间开始即进入必要义务，超过末章保留 overdue。任一关系条件匹配即可触发，必须提供明确 story_time；只使用当前来源中已核实且在角色权限内的状态。该扩展属于规范伏笔接口，旧章纲的 due_chapter 仍兼容。

分支是完整快照形成的新作品，不是共享可变章指针。选择返回的新 book ID 即在子分支继续；原稿及父分支保持独立。不会复制运行租约或计费记录。

## 本地语义检索

安装可选依赖：`.venv/bin/python -m pip install '.[semantic]'`。准备本地 SentenceTransformer safetensors 模型目录，系统不自动下载模型，不信任远程代码。

`retrieval-config --file retrieval.json` 配置示例：

```json
{"strategy":"hybrid","model_path":"/absolute/local-embedding-model"}
```

可选 lexical、semantic、hybrid。保存时校验模型目录并固定完整工件指纹；模型改变须重新配置和建索引。配置 HTTP 为 GET/PUT `/api/retrieval/config`。运行 `memory BOOK semantic-index --batch-size 32`，每次最多处理一个批次，返回 `remaining`；显式重复执行直到为 0。HTTP 为 POST `/api/books/BOOK/memory/semantic-index`，默认批次 32。

定稿仅入本地待处理队列，不在写事务内编码。索引按作品/模型隔离，候选经 Core 重新检查权限和当前版本。首次任务及补查共用检索器；一跳关系扩展仅基于明确实体关系及 story_time，最多 16 个邻居、32 条可选证据。显式硬依赖不受普通检索 top-k 截断。

## 可复现验收

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m story_core.state_machine_benchmark --chapters 500 --mode adaptive --output test-results/state-machine-500.json
.venv/bin/python -m story_core.retrieval_evaluation --store /absolute/authorized-fixture --labels labels.jsonl --summary summaries.jsonl --output test-results/retrieval.json
```

评测标签逐行包含 `case_id`、`independent_query_id`、`query`、`scope`、`expected_ids`、`forbidden_ids`、`category`，可补充 `hard_required_ids`。同一独立问题的距离检查点单列。摘要评测工件是人工核实的 JSONL，字段 `verified:true`、`book_id`、`text`、`source_ids`、`visibility`；不是把源记忆自动当作摘要。只有加 `--allow-local-model` 才执行本地语义模型；没有模型或完整索引时对应策略不可用。

状态机实验使用确定性合成结果，衡量流程和容量，不衡量模型的理解或写作水平。质量评测需要独立人工标签和实际模型工件；未测指标保持 null，不从别名匹配或随机权重测试推导语义召回率。
