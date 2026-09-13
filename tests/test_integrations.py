import hashlib
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]


def test_host_packages_share_exact_skill_body():
    source = ROOT / "skills" / "hulk-story" / "SKILL.md"
    copies = [
        ROOT / "integrations" / "claude-code" / "skills" / "hulk-story" / "SKILL.md",
        ROOT / "integrations" / "codex" / "skills" / "hulk-story" / "SKILL.md",
        ROOT / "integrations" / "dsh-hulk-story" / "skills" / "hulk-story" / "SKILL.md",
    ]
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digest for path in copies)
    text = source.read_text(encoding="utf-8")
    assert "hulk-story" in text and "story_next" in text and "story_submit" in text


def test_host_packages_share_skill_protocol_resources():
    source = ROOT / 'skills' / 'hulk-story' / 'references' / 'task-protocol.md'
    copies = [
        ROOT / 'integrations' / 'claude-code' / 'skills' / 'hulk-story' / 'references' / 'task-protocol.md',
        ROOT / 'integrations' / 'codex' / 'skills' / 'hulk-story' / 'references' / 'task-protocol.md',
        ROOT / 'integrations' / 'dsh-hulk-story' / 'skills' / 'hulk-story' / 'references' / 'task-protocol.md',
    ]
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digest for path in copies)
    skill = (ROOT / 'skills' / 'hulk-story' / 'SKILL.md').read_text(encoding='utf-8')
    assert '自然语言' in skill and 'context_manifest' in skill and 'task-protocol.md' in skill


def test_claude_and_codex_manifests_reference_stdio_server():
    claude = json.loads((ROOT / "integrations" / "claude-code" / ".claude-plugin" / "plugin.json").read_text())
    assert claude["name"] == "hulk-story"
    claude_mcp = json.loads((ROOT / "integrations" / "claude-code" / ".mcp.json").read_text())
    assert claude_mcp["mcpServers"]["hulk_story"] == {"command": "hulk-story", "args": ["mcp", "serve"]}
    codex = json.loads((ROOT / "integrations" / "codex" / ".codex-plugin" / "plugin.json").read_text())
    assert codex["name"] == "hulk-story"
    config = (ROOT / "integrations" / "codex" / "mcp.toml").read_text()
    assert 'command = "hulk-story"' in config and 'args = ["mcp", "serve"]' in config


def test_dsh_bundle_registers_packaged_skill_and_mcp_bridge():
    folder = ROOT / "integrations" / "dsh-hulk-story"
    package = json.loads((folder / "package.json").read_text())
    assert package["dsh"]["bundle"]["patch"] == "./cordis.patch.yml"
    assert {"cordis.patch.yml", "skills", "lib"} <= set(package["files"])
    patch = yaml.safe_load((folder / "cordis.patch.yml").read_text())
    serialized = json.dumps(patch)
    assert "@deepseek-ai/dsh-mcp-client" in serialized
    assert "hulk-story" in serialized
    loader = (folder / "lib" / "index.js").read_text()
    assert "skills/hulk-story/SKILL.md" in loader
    assert "ctx.skills" in loader and ".register" in loader


def test_distribution_files_declared_by_manifests_exist():
    folder = ROOT / "integrations" / "dsh-hulk-story"
    package = json.loads((folder / "package.json").read_text())
    for entry in package["files"]:
        assert (folder / entry).exists()
