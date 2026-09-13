<p align="center">
  <img src="story_core/web/wenqu-logo.svg" width="80" alt="WenQu Logo" />
</p>
<h1 align="center">WenQu · 文曲</h1>
<p align="center">从一个念头，到一部持续生长的小说。</p>

WenQu 是面向中文小说创作的本地 AI 工作台，将开书、规划、正文生成、审稿返修、故事记忆和章节听书整合在同一项目中。可通过 GUI 使用，也提供 CLI、MCP 与 Skill 接入能力。

> 当前为持续迭代的早期版本。适合个人创作与 Agent 工作流探索；生成质量、平台投稿适配和各宿主兼容性仍需实际验证。

## 核心特点

- **按作品管理创作**：自定义题材、人物、世界观和规则；支持章节导入、归档、回收站与还原。
- **可恢复的写作流程**：按章或连续生成，支持暂停和断点续写；保留候选稿、正式版本与修改差异。
- **有边界的 AI 审稿**：按问题优先级处理反馈，基础审稿最多三轮；达到上限自动通过时明确标记，作者仍可提出修改意见。
- **有来源的故事记忆**：人物、设定、伏笔关联正文证据与章节版本，支持分类筛选和上下文检索。当前使用词法检索，而非向量数据库。
- **中文表达规范**：内置章节标题、中文去模板化写作和常见 AI 句式参考规范；不承诺通过特定 AI 检测器。
- **多种模型与宿主入口**：支持 OpenAI-compatible API、Claude Code 执行器，以及 Claude Code、Codex、DeepSeek Harness 的 Skill/MCP 接入配置。
- **听书与专注阅读**：Edge TTS 中文音色、音量与语速控制、自动下一章，以及播放至 70% 时预加载。
- **用量与导出**：按作品、章节查看已报告 Token 用量，导出 TXT、Markdown 和 EPUB；用量统计不等于服务商最终账单。

## 快速开始

需要 **Python 3.10+**。当前主要在 macOS 环境验证；网页保存 API Key 使用 macOS 钥匙串，其他系统建议使用环境变量配置。

```bash
git clone https://github.com/Moosphan/WenQu-story.git
cd WenQu-story
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[tts]'
wenqu --root ./books serve --port 8765
```

浏览器打开 **http://127.0.0.1:8765/**，进入右上角「AI 配置」，选择模型连接后新建作品。Windows 可通过 `.venv\Scripts\Activate.ps1` 激活环境。

听书需要连接微软语音服务；点击播放及后续预加载会发送对应章节文本。无需听书时可使用 `pip install -e .`。

## 模型配置

**工作台配置**：选择服务商，填写接口地址、模型和 API Key；Claude Code 模式沿用本机安装与登录状态。配置可用性以实际调用结果为准。

**环境变量配置**（名称保留兼容前身版本）：

```bash
export HULK_BASE_URL='https://api.openai.com/v1'
export HULK_MODEL='your-model'
read -s HULK_API_KEY
export HULK_API_KEY
wenqu --root ./books serve
```

执行 `read` 时粘贴自己的密钥并回车。变量需要设置在启动服务的同一个终端；项目不会自动加载 `.env`。

已有 Claude Code 登录环境也可使用：

```bash
HULK_EXECUTOR=claude wenqu --root ./books serve
```

Codex 与 DeepSeek Harness 接入见 [集成目录](integrations/)；独立 Skill 入口为 [SKILL.md](skills/hulk-story/SKILL.md)。部分宿主资产保留 `hulk-story` 名称以兼容已有配置，DSH 需按目标版本验证。

## 开发与数据

```bash
pip install -e '.[test,tts]'
python -m pytest -q
node --check story_core/web/app.js
```

JavaScript 检查及部分测试需要 Node.js。核心代码位于 `story_core/`，界面位于 `story_core/web/`，适配配置位于 `integrations/`。

作品数据库、日志、导出和音频缓存默认保存在 `books/`。该目录、密钥文件、本机配置与个人作品已加入忽略规则；升级前建议备份书库。切勿将 API Key、私人作品或真实服务日志提交到仓库。

本项目提供本地创作能力，不包含在线账号、多租户隔离或平台自动投稿。欢迎通过 Issue 提交可复现问题，或通过 Pull Request 改进工作流与集成。

## License

源码与本仓库自有 Logo 使用 [MIT License](LICENSE)。第三方依赖遵循各自许可证；模型及语音服务遵循其服务条款，用户作品不因使用本工具而自动适用 MIT。
