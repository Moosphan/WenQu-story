# 有界上下文与长期记忆：实施进度

基线：`9f1852e`。工作分支：`codex/bounded-context-memory`。
本次只在隔离 worktree 开发，未访问私人正文/数据库/凭据，未操作原工作区或 8765 服务。

## 约束与范围

`docs/17-bounded-context-memory-architecture.md` 的“已确认的实施修订”优先。默认 shadow 只测量；分层预算可借用，24K 是未校准软目标；输出与额外开销另行预留。不会触发 AI 压缩、自动付费回放或增加内容审核次数；不在结果提交时重新检查新预算。

已在基础提交 `245b1c3` 上继续完成作者核实、维护与任务补查集成；**不是 P0–P3 全部验收完成**。没有真实 500 章生成、普通同义线索 95% 召回或文学质量提升的证据。

## 任务账本

|阶段|本次完成|仍待完成|
|---|---|---|
|P0|统一 ContextCompiler；off/shadow/adaptive；阶段软目标、最终消息/schema/系统指令计数；软配额共用；硬依赖保留与缺失诊断；容量与输出/开销预留；任务与服务商日志；GUI 用量展示；JSONL 离线回放；输入/输出/缓存用量分离及离线校准报告入口|真实请求 tokenizer 校准；宿主隐性开销及输出上限的实测；小规模真实续写放量|
|P1|SQLite 7→8→9 增量结构；稳定实体/事实 ID；显式别名与歧义；已核实状态投影；故事时间/角色信念分离；伏笔调度与来源版本绑定的状态事件；来源及传递依赖失效；保守、幂等的本地迁移；自适应任务可引用显式状态；规范属性/别名注册；来源候选、隔离、作者核实/拒绝、冲突确认、歧义绑定与审计；持久维护队列、原文摘录及索引整理|完整实体合并/重定向；失效依赖的语义修复；真正的章/卷语义摘要；真实多正文分支；伏笔回收时间区间及关系触发扩展|
|P2|中文双字/词倒排索引；每通道最多 64 候选；可替换语义 ID 适配器及 RRF 融合；Core 再做作品/版本/POV 过滤；每 run/章最多 2 次租约补查，接入任务/provider/CLI/HTTP/MCP；替换证据装箱；GUI 可选择有界索引|语义服务/关系扩展未启用；首次领取前的缺失硬依赖不能依靠模型补查修复；普通远期召回质量标注|
|P3|构造 100/300/500 章历史快照，覆盖 7 阶段共 21 请求；新增 SQLite 递增资料的 1–500 章、7 阶段共 3500 个编译点；指定第 12 章债务依赖；单元测试覆盖改名、信念、倒叙、转手、改章、歧义、义务拥挤；不调用模型|真实历史样本授权回放；实际完整任务状态机与提供商增长曲线；全历史/语义摘要/向量/混合策略质量对比；真实成本与文风衔接评估|

## 启用与回滚

默认环境无需更改，即 `HULK_CONTEXT_MODE=shadow`。shadow 不裁剪发出的资料，沿用现有财务预留、180000 字节传输门槛和任务接收规则；因此旧超大请求仍可能遇到原门槛。新 `context_diagnostics` 和来源 manifest 均不发给模型。

`HULK_CONTEXT_MODE=off` 回到旧资料选择并关闭本地候选装箱，保留基本测量。该开关回滚的是上下文策略；SQLite 9 不自动降级为旧程序的 SQLite 7/8。已领取任务固定其策略与资料，下一个任务才读取新模式，避免处理中请求被重新裁剪。

实验性启用 `HULK_CONTEXT_MODE=adaptive` 前，显式配置 `HULK_CONTEXT_WINDOW_TOKENS` 为当前选用模型的实际可用窗口。缺少容量时不给出“可以执行”的结论。`HULK_CONTEXT_MODEL_WINDOWS` 可提供 JSON 模型名到容量的映射；启用映射后，实际传输模型缺项时不借用别的模型额度。任务发放仍需通用窗口配置，传输前再按实际模型复核。

其他参数：`HULK_CONTEXT_SOFT_TOKENS`（覆盖阶段软目标）、`HULK_CONTEXT_OUTPUT_TOKENS`（输出预留，任务发放至少保留内置传输的 12000）、`HULK_CONTEXT_OVERHEAD_TOKENS`（额外开销预留，默认 4000）。兼容 API/原生传输实际输出上限为既有 12000，发送前按该上限复核。复杂章节的硬资料可以突破软目标，只要仍能放入实际配置窗口与剩余财务预算；硬资料放不下时保留全文并报告，不静默截断。

默认 Counter 是 UTF-8 字节分词器的宽松上界估算，明确标记 `utf8-byte-upper-bound-uncalibrated`，**不是精确 tokenizer 或账单**。它计量完整序列化消息而非仅正文，也不应被解读为“中文字符等于 token”。接口可注入匹配模型的 TokenCounter。CLI 宿主内部 schema 包装、隐藏指令与输出控制尚有不透明开销，必须保留余量并实测。

内容审核仍采用原有最多 3 次及上限标记通过；网络/格式失败仍暂停当前阶段，不新增自动重试或重置内容审核次数。单章人工确认、连写默认确认与断点续写继续由既有状态机管理。

原有财务 token 预留与 `provider_usage` 账单分开：失败/截断请求有用量时仍计入，未知值不填 0。整理触发率不是失败率。未标注的关键事实遗漏率为 null。`context_usage` 聚合服务端编译尝试（含容量、财务与传输门槛前的拦截测量）；整理后不可执行率只使用可判定样本，未知容量另计。实际拦截按 gate 分类，真实传输计数及最终容量失败另存于 provider call 的 `context` 并单独统计，避免与任务发放重复计数。

## 状态与迁移

规范化 API 位于 `story_core/long_memory.py`，接受 Core 的 SQLite 事务连接。`record_fact(..., verified=False)` 默认不进入当前状态；原文引用匹配不代表事实已核实。规范谓词及 `verified=True` 必须由可信调用方明确决定。现有模型抽取协议不会自动将新说法提升成核实状态。

章节计划可选 `entity_ids`、`required_fact_ids`、`promise_ids`、`trigger_keys`、`story_time`。自适应模式读取本章显式实体与时间的已核实状态，以及到期/触发/明确引用的伏笔。缺少明确事实 ID 会出具缺失诊断；故事时间不能猜成章节序号。旧章替换保留原文版本，失效对应及传递依赖状态，并沿用后续章节 needs_review 屏障。

`migrate_legacy(conn, book_id)` 仅在可信维护流程中显式调用；保留全部原记录、来源和无法确定的记忆，不自动按文字相似性合并。不自动迁移私人数据库。旧库关键词索引可用 `rebuild_index(conn, book_id)` 本地补建；新章提交增量索引，检索报告 index_complete，缺索引不等于没有该事实。

`bounded_lookup` 保留为 Core 辅助函数。实际入口 `lookup_task` 会核实 task/lease/worker、运行状态、作品版本、章节和 POV，已接入模型 JSON 结果循环及 HTTP/CLI/MCP。每章两次补查记录持久化；每个模型轮次仍计量用量/步骤，内容审稿次数不变。回执只含元数据，证据在下一个任务中替换装箱；首条请求证据可突破软目标，真实容量不足则在下一次模型调用前阻断。`lookup_result.delivered_count` 是实际发出的证据条数。默认 shadow 不启用补查。模型输入边界已装箱，不代表服务端所有工作都 O(1)：旧 canonical_memory、全书计划及索引常见词查询仍会随资料规模增加处理成本。

## 离线验证

```sh
.venv/bin/python -m pytest -q
node --check story_core/web/app.js
.venv/bin/python -m story_core.context_replay --synthetic --output test-results/context-replay.json
# 获得授权后的历史请求可按每行一个 task 的 JSONL 回放；不会调用 provider。
.venv/bin/python -m story_core.context_replay --input authorized-tasks.jsonl --output test-results/replay.json
```

报告只保留计数、诊断和引用 ID，不复制输入正文。JSONL 可含 `expected_hard_ids`、`reported_tokens`、`truncated_output`；没有标签或服务商用量的字段不制造成功率/成本。

初步构造回放：21 个请求，整理触发 21/21，整理后容量不足 0/21，指定硬依赖缺失 0/42，真实模型调用 0，账单未知。上述是刻意构造的测试结果，不是产品失败概率。计数如下（含序列化消息/schema，单位为未校准的保守 token 估算）：

|历史规模|全历史输入范围（7 阶段）|装箱输入范围（7 阶段）|
|---|---:|---:|
|100 章|98,370–108,541|15,966–23,991|
|300 章|286,772–305,343|15,968–23,993|
|500 章|475,172–502,143|15,968–23,993|

该曲线只覆盖指定样本；大量真正到期的硬义务可以突破软目标并触发容量诊断，不能用这组结果宣称任意章节均能执行。产品原有 200 章开书/导入上限暂未放开；500 章是离线历史规模，尚未对真实工作台开放。

基础提交验证：380 项 pytest 通过（较基线新增 87 项）；`node --check story_core/web/app.js`、`git diff --check` 均通过。仅有两项既有 Starlette/httpx/AnyIO 弃用警告。独立审查已修复人物约束丢失、未知模型容量回退、阻断漏统计、伪文本键匹配、伏笔权限与终态溯源/乱序回填问题。

## 第二批：可操作流程与验证

资料库提供作者事实登记、来源版本/故事时间/可见范围预览、待核实/接受/拒绝/隔离分页、歧义实体选择和显式冲突确认。只有作者勾选核实并接受才写入已核实事件；原文匹配不代表语义为真。绑定/接受会递增作品修订并取消旧修订租约，保留候选正文、当前阶段和三次内容审稿计数。重复请求复用原审计结果，不取消新修订任务。旧来源候选可以拒绝清理，但不能接受。

公共入口：

- HTTP `/api/books/{book}/memory/{entities,proposals,maintenance}`，以及候选的 `/decision`、`/bind`。
- CLI `memory BOOK list|propose|decide|bind|entity|maintenance|maintain`。`decide` 需要 request ID、expected revision、actor；接受还需显式 `--verified-by-author`。
- MCP `story_memory_propose`、`story_memory_proposals`、`story_memory_entities` 只提交/查询候选，不提供接受事实工具。候选由宿主主动提交或作者登记；既有自动抽取结果不会被悄悄提升为已核实事实。
- 补查 `story_lookup` / CLI `lookup TASK --lease ... --query ... --reason ...`，或单独提交 `context_lookup` 结果。`story_query` / CLI `query --strategy bounded` 可显式选择索引；附带任务凭据时强制继承任务作用域。

新定稿自动加入本地整理队列，旧章替换同时失效并排队传递依赖。GUI“整理下一批”或 CLI `memory BOOK maintain --backfill --limit 5` 只处理有界批次；稳定来源作业最多三次失败尝试，不因再次排队清零。同一事务补建中文倒排索引和来源摘录，处理前后复核版本指针。失效语义不会因索引重建变成有效。`ready` 仅表示索引/摘录就绪；`needs_verification` 仍须核实。

Core 另提供旧记忆显式绑定转候选、规范属性注册、最多 5 个近期来源摘录和 3 个卷级来源锚点的浏览视图；摘录单条最多 2048 字节，不递归概括，也不默认进入读者/POV 提示。这些不等价于自动语义摘要、实体合并或依赖修复。GUI 手动登记目前只创建客观状态；宿主提交的角色信念/作者计划可以显示其所属角色、范围和有效时间后核实。

新增离线命令：

```sh
.venv/bin/python -m story_core.memory_benchmark --chapters 500 --output test-results/memory-growth-500.json
```

实验在临时 SQLite 中逐章加入合成资料，读取当前来源，测量七个阶段的全历史、受控装箱和最近五条原文摘录基线；临时库自动清理，报告不含正文。**3500 个点是编译实验，不是 3500 个真实模型请求或完整写作状态机回放。**

|章节检查点|全历史输入范围|有界输入范围|
|---|---:|---:|
|100|118,597–123,191|15,311–23,903|
|300|358,997–363,591|15,311–23,903|
|500|599,397–603,991|15,311–23,903|

单位仍为未校准的保守 token 估算。指定第 12 章来源从第 13–500 章共 3416 个阶段标签均保留；最近五条摘录基线缺失 3381/3416。确切词与显式登记别名各命中 488/488，**这些是同一条构造来源随距离增长的重复标签，不是 488 个独立语义问题，也不支持普通同义线索 95% 的结论**。500 条记忆均建索引，容量阻断 0，真实模型调用 0；语义召回、真实 token 及账单均为 null/未知。逐点编译/检索耗时记录在报告，服务端历史扫描复杂度仍未消除。

第二批独立审查发现并修复：任务查询作用域缺失、补查证据全被裁掉、维护遗漏旧倒排索引、过期候选无法拒绝、GUI 歧义错误码丢失。隔离端口 18769 的合成工作台已实际验证接受、歧义绑定和本地整理；未访问 8765 或私人作品。

第二批最终回归：419 项 pytest 通过（相对基础提交增加 39 项），仅两项既有依赖弃用警告；JavaScript 语法及差异检查通过。MCP 实际 stdio 与 CLI 子进程覆盖补查及候选入口。浏览器合成验收还覆盖明确拒绝和绑定后实体名称/角色范围展示，测试服务与页面已关闭。benchmark 的 `retrieval_ms` 每章测一次并复用到七个阶段，汇总须按章去重，不能当作 3500 次独立检索计时。

## 第三批：真实校准的离线准备

API、原生 DeepSeek 和 Claude CLI 在既有 `provider_usage` 日志中新增 `usage_breakdown`，单独保存输入、输出和已缓存输入数量；总用量字段保持既有含义。兼容 API 的缓存是输入的一部分，不能重复加算。Claude 原生用量仅在非缓存输入、缓存读取、缓存创建三项全部明确返回时合计输入，缺项保持未知。服务商字段矛盾时不用于输入校准。截断/解析失败但已返回用量的调用也保留记录，下一次调用重置字段，避免沿用旧值。

`story_core.context_calibration` 仅分析显式指定的 JSONL，每行是一个 `provider_usage` 的 payload；不自动读取作品数据库、宿主配置或凭据，不发起模型调用。最低有效输入如下（数字仅为格式示例）：

```json
{"call_id":"sample-1","executor":"api","context":{"model":"exact-model-name","counter":"utf8-byte-upper-bound-uncalibrated","final_tokens":1000,"fingerprint":"recorded-request-fingerprint"},"usage_breakdown":{"input_tokens":800,"output_tokens":100,"cached_input_tokens":200}}
```

```sh
.venv/bin/python -m story_core.context_calibration --input authorized-usage.jsonl --output test-results/calibration.json
```

报告按实际执行器、模型、计数器分组，输出有效调用数、输入低估次数、最大低估 token 和最大“服务商输入/估算输入”比例。同一 call ID 的完全一致测量只计一次，冲突测量整组排除；总 token 不冒充输入 token。宿主报告多个模型或模型与估算不一致时排除该调用，未提供明确模型/计数器/请求指纹的记录也不进入校准。报告不包含输入正文、原始响应、请求指纹或凭据，但会保留模型和执行器标签。

这是**采样和分析工具就绪**，不是已经完成真实 tokenizer 校准。提供的数据来源仍需调用者保证，样本统计不能证明某个容量安全，也不能据此自动降低余量或开启 adaptive。仍需获得明确授权的样本和模型调用范围，才能执行真实续写与成本评测；当前默认 shadow 和“不调用真实模型、不读取私人作品”的边界保持不变。

第三批回归：427 项 pytest 通过（新增 8 项校准测试），两项既有依赖弃用警告；差异检查通过。使用 HTTP mock、合成 worker 和临时 JSONL 验证，不调用真实模型。覆盖缓存计数、截断用量、重复/冲突排除、模型归属、跨轮清空、正文不进入报告及禁止覆盖输入文件。
