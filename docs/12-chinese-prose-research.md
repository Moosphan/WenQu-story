# 中文正文去AI味：开源调研与采纳说明

调研时间：2026-09-13。检索 GitHub 仓库说明、公开 issue/release，并核对可访问仓库的 GitHub API pushed_at。推送日期不等于规则更新日期，不用搜索引擎“近期抓取”冒充近期提交。没有安装或执行外部仓库代码，没有将作品上传检测平台。

## 调研结果

| 项目 | 活跃度证据 | 可参考内容与局限 |
|---|---|---|
| [op7418/Humanizer-zh](https://github.com/op7418/Humanizer-zh) | API pushed_at：2026-01-19 UTC | 通用写作模式排查，可借鉴识别套话的思路；不是近期持续更新的保证。中文网文不能直接套用百科/通用文章全部规则 |
| [chinese-ai-humanizer](https://github.com/Wechat-ggGitHub/chinese-ai-humanizer) | API pushed_at：2026-08-23 UTC | 仓库定位是较精简的中文去AI味提示，启发是控制提示负担，而不是把整个词表塞进每章 |
| [oh-story-claudecode](https://github.com/zenstory-ai/oh-story-claudecode) | API pushed_at：2026-09-12 UTC；README列v0.7.10/2026-09-09 | 与网文场景最相关，README明确区分改善读感与绕过检测器。保留剧情功能的方向值得采用，不引入额外阻断审稿链 |
| [AIWriteX releases](https://github.com/iniwap/AIWriteX/releases) | API pushed_at：2026-09-11 UTC | 发布记录有提高朱雀通过效果的宣传；本轮所读页面没有足以独立复现的同版本、固定样本对照材料，因此只记录主张，不认定有效 |
| [humanize-chinese](https://github.com/voidborne-d/humanize-chinese) | 检索索引可见，直接页面/API本次读取失败 | 索引中的README明确说明用自有检测公式与HC3回归，不能接入朱雀作为oracle。内部评分改善不能推导外部检测通过。未采用其代码或规则词表 |

许可证元数据：Humanizer-zh/oh-story 为 MIT，chinese-ai-humanizer 为 CC-BY-4.0，AIWriteX 为 Apache-2.0（查询时）。本次独立编写项目规范和示例，仅记录思路与出处，不复制上游代码或大段规则。

## 关于“反朱雀有效”的证据等级

[Humanizer-zh issue #12](https://github.com/op7418/Humanizer-zh/issues/12)（2026-02-28）有使用者反馈不能通过朱雀；这只是个案，既不能证明所有样本无效，也不能支撑稳定有效。[oh-story的用户讨论 #205](https://github.com/worldwonderer/oh-story-claudecode/issues/205)同样出现去AI味后仍被判断疑似AI的反馈；其现有README将 deslop 定位为写作检查。

本次没有找到并验证一套足以承诺稳定降低朱雀结果的公开对照证据。项目宣传、个别截图、自己定义的AI味分数、外部检测结果、读者喜好是不同层次，不能互相替代。尤其本项目此前已经出现审核无限消耗，不能新增“检测不过就再改”的循环。

## 采纳与舍弃

采纳：具体诊断、最小有效修改、保留作者声线和事实、区分叙事视角、删重复解释、控制提示长度、改后保留差异。

舍弃：固定短句比例、固定删文比例、固定语气词频率、把所有情绪改成动作、强制网络口语、故意语病和隐藏字符、靠反复提交检测器来筛选版本。这些做法可能形成新的模板并损害长篇连续性。

本地旧 story-deslop 中的段落/对话标签比例和语气词示例不作为本项目硬指标；不修改用户全局安装的该 Skill。本仓库的中文正文规范以小说场景独立维护。

## 落地与验收

规则文件：`skills/hulk-story/references/chinese-prose-deslop.md`，版本 prose-v1。轻量摘要进入正文、返修、普通审稿与默认 bounded 综合审稿；完整说明供 Skill 阅读，三类宿主副本同步。不新增任务阶段、输出字段或检测调用。

后续实测建议：准备不少于12个不同场景的固定片段/章节，含对白、动作、过渡、情绪回收；同时加入经作者授权的自然原稿作为误报观察。隐藏前后版本身份让读者比较，记录事实变更、声线保持、连贯性和接受偏好。样本量不足时只报告样本内结果，不宣传通过率。外部朱雀对照另行按规范记录，不作为文学验收替代品。

本轮验证：268项自动测试通过（2项既有第三方弃用警告）。新增测试验证 prose-v1 实际进入草稿与默认综合审稿，流程仍为 brief→outline→draft→extract→continuity→awaiting_author，没有额外检测或润色阶段。三种宿主Skill副本已同步，服务在无活动任务时更新。本次未新增模型生成或外部检测调用。
