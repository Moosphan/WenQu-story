"""Never read the author's live model selection from an automated test."""
import pytest


@pytest.fixture(autouse=True)
def isolated_ai_settings(tmp_path, monkeypatch):
    from pathlib import Path
    from story_core import codex_discovery
    read_selection = codex_discovery.local_selection
    monkeypatch.setattr(codex_discovery, 'local_selection',
        lambda home=None: read_selection(tmp_path if home is None or Path(home) == Path.home() else home))
    monkeypatch.setattr('story_core.codex_discovery.executable_candidates', lambda: [])
    monkeypatch.setenv('HULK_AI_CONFIG_PATH', str(tmp_path / 'ai-settings.json'))
    for name in ('MODE', 'SOFT_TOKENS', 'WINDOW_TOKENS', 'OUTPUT_TOKENS', 'OVERHEAD_TOKENS', 'MODEL_WINDOWS'):
        monkeypatch.delenv('HULK_CONTEXT_' + name, raising=False)
