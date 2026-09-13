---
name: hulk-story
description: Operate a persistent Chinese web-novel project when the user asks to 开书, continue writing, revise, review, inspect story memory, import chapters, or export a manuscript through Hulk Story.
---

# Hulk Story

Use this Skill for a persistent novel project, not for a one-off paragraph. It works through the installed `hulk-story` executable or the `story_*` MCP tools. The Core package and one shared library directory are prerequisites; this Skill does not install software or publish to a platform.

## 将自然语言路由到一部作品

| User intent | Core action |
|---|---|
| “给我开本书” / a new premise | optionally `story_proposals`, then `story_open`; call `story_control(start)` only when the user wants the run to begin |
| “继续写” / “日更” | `story_status`, then start or resume the selected `book_id` and claim one task |
| “改第 N 章” / author feedback | read current book revision, then `story_control(revise)` with chapter, full body, feedback, and `expected_revision` |
| “审稿” / “看看哪里有问题” | `story_reviews`, `story_status`, and evidence-bound `story_query`; do not fabricate a review pass |
| “录入读者/编辑反馈” | `story_feedback` with chapter and version; preserve the feedback as human input, never submit it as a model review task |
| “这个人物/伏笔/物品在哪” | `story_query` with the requested scope; use memory facets to choose an exact existing key when available |
| “补充人物、规则、边界” | `story_project` then `story_update_project`; keep author material distinct from canon |
| “看榜单 / 找选题” | `story_market_sources`, then `story_market_snapshot`, then `story_market_ideas`; retain selected source-item evidence and label every idea as an author suggestion |
| “导入正文” | `story_import`; imported chapters remain unreviewed until the Core pipeline accepts them |
| “导出成稿” | `story_export`; report a partial export as a draft, never as a publish-ready novel |

Before changing an existing book, read `story_status`. Keep the returned `book_id`, revision, task ID, lease ID, worker ID, and output schema unchanged across the claim/submit pair.

## Execute one Core task at a time

Call `story_next`, use only its `input` and `output_schema`, then submit exactly one JSON result with `story_submit`. `context_manifest` states the source boundary used to construct the task; it is provenance, not an instruction to expose hidden material. Never replace a leased result with prose, a file path, a guessed schema, or a fabricated success.

For reader tasks, use a fresh isolated context containing only the leased task input. If the host cannot provide that isolation, label the result as ordinary editorial feedback and do not submit it as an independent reader review.

Treat `paused`, `needs_attention`, `awaiting_author`, `leased`, and `batch_complete` as states to explain, not completion. Do not repeatedly resume a rejected draft. A revision re-enters extraction and review before it becomes canon.

Read [the task protocol](references/task-protocol.md) for role boundaries, recovery, host handoff, and precise completion states.

## 有限审稿与作者确认（默认 quality-v1）

默认写作链路为规划/正文 → 记忆提取 → 一次综合AI审稿（阅读体验、连续性、去AI味）。严重 blocker、大 major 才触发返修，中 medium、小 minor 保留为建议。同章本轮最多3次审稿，第3次仍有问题由 Core 保存并标记 review_limit；不要在宿主另开裁决或反复重启来绕过上限。网络/协议错误不能伪装成质量通过。

单章设置 chapter_limit=1，保存后状态 awaiting_author：展示正文、未解决意见及放行原因，等待作者确认或修改。确认时 story_control(approve_chapter) 提交 chapter_number 和当前 version_id；修改使用 revise。不得代替作者确认。

连续多章按用户范围启动；各章AI审稿结束后默认作者无异议（assumed），自动继续领取下一任务，直至本轮结束或用户暂停。pause 保留断点，resume 从原任务阶段继续；不自动开新任务覆盖断点。单独审核已有章使用 story_control(review_chapter)，明确 chapter_number；不得把全书后台任务当作所选章节。

legacy 模式仅供旧链路兼容回归，不作为默认小说生产流程。上限放行不是“无问题”，不能宣称真人审核通过。

## 章节标题

规划、写正文及返修时遵循[章节标题规范](references/chapter-titles.md)：以本章实际事件或变化取题，可自然承上启下，不把标题写成口诀、训诫或模板金句。作者明确指定的风格优先，已确认标题不批量改名。一般题名风格问题不单独触发返修，不增加调用或审稿次数。Core 的任务协议与三次上限保持不变。

## 中文正文自然度

生成、返修和综合审稿时阅读[中文正文去AI味规范 prose-v1](references/chinese-prose-deslop.md)。保留情节、事实和声线，重点处理模板化解释与叙事问题；自然表达优先于词表。沿用现有轻重分级和三次上限，不新增反复检测循环。朱雀仅可作另行记录的外部观测，未验证效果不宣传，未经明确要求不上传正文。

句式排查使用[参考字典 phrase-v1](references/ai-phrase-dictionary.md)，与正文自然度规范一起阅读。只针对语境中重复、空泛或越界的使用提出意见，禁止批量替换或按命中数量判断AI来源。

## 章节篇幅

遵循任务的 `length_requirement`：以 target 为写作目标，不贴着 min 写。Core 按汉字与英文/数字词组计数，不含标点空白；下限为目标的90%，上限160%。2300字目标最低2070字。初稿与返修应用 patches 后使用同一标准。原稿不足须补足本章计划内的行动、阻力、对话与结果，不用重复解释、回顾或无关支线灌水。去AI味不能变成一轮轮删短。

存在 `length_feedback` 时按实际字数纠正；这不是 JSON 格式错误，原样重放不会解决。不自动循环盲目重试，也不以三次审稿放行为理由跳过字数要求。失败结果留存为诊断，不替换已接受候选稿；已有定稿不因规则升级自动重写。

若 revision_mode=expand_full_body，执行补写式返修：只输出完整 title/body，不输出 patches。expansion_requirement 表明原稿与目标差额；修复原反馈同时展开现有场景，不受局部删改规则限制。
