# 离线本地语义检索实现报告

日期：2026-09-19。只使用测试库与随机合成微型模型；未读取生产作品、未下载预训练权重、未调用远程模型或付费服务。

## 已实现

- `retriever_for(store)` 读取书库根目录 `retrieval-policy.json`；默认 `lexical`，无模型加载/编码调用。`semantic` 仅向量候选，`hybrid` 为关键词与向量 RRF。
- `configure_retrieval(store, {"strategy":"semantic", "model_path":"/absolute/local/model"})` 在持久化前验证依赖、safetensors、本地模型加载，记录内容 SHA-256 指纹。实际加载参数：`local_files_only=True`、`trust_remote_code=False`、`model_kwargs={"use_safetensors":True}`。不接受远程模型标识；失败不回退伪语义。
- 可选依赖 `pip install -e '.[semantic]'`。已在本机验证 sentence-transformers 5.7.0 / transformers 5.17.0 / hnswlib 0.8.0 的真实加载调用。
- 章提交仅追加 `semantic_pending`；显式 `maintain_semantic_index(store, book_id, batch_size=32)` 分批读当前版本，事务外编码/构建 HNSW，写事务内复核版本后发布元信息。旧库可通过同一入口回填。
- 分区为 book / 模型指纹；SQLite 保存 memory_id、source_version、dimension、normalized vector、indexed_status 和 ANN label，HNSW 文件按不可变 generation 发布。指纹变化需重新配置，不能混用旧分区。维护锁串行化同分区维护。
- HNSW 返回至多 64 个 ID，Core 再次按同一检索事务过滤作品、当前版本、committed、章节边界、reader/POV。HNSW 是近似召回，不保证每个合法命中进入 top-k；失效或不可见候选会占据候选预算。
- `prepare_query(str)` 必须在事务外执行，返回含 `.text`、`.vector`、`.fingerprint` 的 PreparedSemanticQuery；lexical 原样返回 str。语义 `search(raw_string, conn=...)` 明确拒绝，避免隐式事务内编码。主代理负责首次任务和补查的 revision 重验接线。

## 验证和未验收边界

`tests/test_semantic_retrieval.py` 包含缺权重配置拒绝、默认关键词模式、真实 HNSW 持久化/授权过滤、写事务外编码监测、维护期间版本替换、模型指纹分区、提交仅入队测试。

另外，测试从随机参数构造 1 层/8 hidden 的微型 BERT，保存本地 safetensors + SentenceTransformer Pooling 后通过真实 LocalEncoder 编码、维护、查询；测试阶段禁止 `socket.socket.connect`。这是实际加载器和向量数学链路验证，**不是预训练中文语义能力验收**。未安装 optional deps 的环境相应测试显式 skip。

首次模块缺失和缺入队表的测试分别红灯，随后实现转绿。最终运行 `python -m pytest tests/test_semantic_retrieval.py tests/test_bounded_retrieval.py -q`：10 passed（6.25s），含真实 SentenceTransformer 加载测试。系统 Python 的 adaptive 集成测试因缺 jsonschema 导入失败，交由主代理使用完整项目环境统一验证。

真实预训练中文模型、无共同词同义改写召回率、95% 质量目标、真实自然语言基准仍为 external-evidence-pending。没有将随机模型或手工向量结果冒充语义质量。

## 已知运行边界

- HNSW 使用 `ef=128`，控制近似搜索候选和 Python 返回行数；不宣称底层图访问严格 O(1)。冷启动需加载本书分区的本地原生索引。
- 当前每个新 retriever 的首次 prepare 会加载并校验模型；重复请求可由宿主持有 retriever 复用实例。模型加载成本与模型大小相关。
- 旧 HNSW generation 保留以允许并发读；尚无自动离线垃圾回收。无效旧向量的候选影响通过显式重建/后续压缩功能处理，Core 始终过滤它们。
- 索引维护为显式操作，不在正常任务领取时自动遍历/编码历史；新提交在维护前无法被语义通道召回。
