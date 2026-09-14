"""Never read the author's live model selection from an automated test."""
import pytest


@pytest.fixture(autouse=True)
def isolated_ai_settings(tmp_path, monkeypatch):
    monkeypatch.setenv('HULK_AI_CONFIG_PATH', str(tmp_path / 'ai-settings.json'))
