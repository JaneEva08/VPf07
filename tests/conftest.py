"""Тесты идут без сети и без ключей: память и workspace подменяются временной папкой."""
import json
import os
from types import SimpleNamespace as NS

import pytest

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from agent import agent as agent_module  # noqa: E402
from agent import tools  # noqa: E402


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-SECRET")
    monkeypatch.setattr(tools, "ROOT", tmp_path)
    monkeypatch.setattr(tools, "WORKSPACE", ws)
    monkeypatch.setattr(tools, "MEMORY_PATH", tmp_path / "memory.json")
    return tmp_path


def tool_call(name, args, call_id="c1"):
    return NS(id=call_id, function=NS(name=name, arguments=json.dumps(args)))


def message(content=None, tool_calls=None):
    return NS(content=content, tool_calls=tool_calls)


class FakeClient:
    """Отдаёт заранее заданные ответы модели по очереди; исключение из списка — «упавший» вызов."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return NS(choices=[NS(message=item)])


@pytest.fixture
def make_agent():
    def _make(script):
        a = agent_module.Agent()
        a.client = FakeClient(script)
        return a
    return _make
