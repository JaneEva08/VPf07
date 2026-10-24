from agent import agent as agent_module
from agent import tools
from tests.conftest import message, tool_call


def test_plain_answer_saved_to_memory(make_agent):
    a = make_agent([message("Привет!")])
    assert a.ask("Привет") == "Привет!"
    assert tools.load_memory() == [{"role": "user", "content": "Привет"},
                                   {"role": "assistant", "content": "Привет!"}]


def test_tool_call_loop(make_agent):
    a = make_agent([message(tool_calls=[tool_call("calculate", {"expression": "2+2"})]), message("Будет 4")])
    assert a.ask("сколько 2+2?") == "Будет 4"
    tool_msgs = [m for m in a.messages if m["role"] == "tool"]
    assert tool_msgs and tool_msgs[0]["content"] == "4"


def test_assistant_message_has_only_standard_fields(make_agent):
    a = make_agent([message(tool_calls=[tool_call("calculate", {"expression": "1+1"})]), message("2")])
    a.ask("1+1")
    assistant = [m for m in a.messages if m["role"] == "assistant"][0]
    assert set(assistant) <= {"role", "content", "tool_calls"}


def test_note_survives_following_turn(make_agent):
    """Регресс: заметка из save_note затиралась при записи истории в конце хода."""
    a = make_agent([message(tool_calls=[tool_call("save_note", {"note": "Любит зелёный чай"})]),
                    message("Запомнила"), message("Ок")])
    a.ask("запомни: люблю зелёный чай")
    a.ask("ещё вопрос")
    notes = [m["content"] for m in tools.load_memory() if m["role"] == "note"]
    assert notes == ["Любит зелёный чай"]
    new_agent = agent_module.Agent()
    assert "Любит зелёный чай" in new_agent.messages[0]["content"]


def test_empty_answer_not_saved(make_agent):
    a = make_agent([message(None), message("Нормальный ответ")])
    before = len(a.messages)
    assert a.ask("вопрос") == agent_module.EMPTY_ANSWER
    assert tools.load_memory() == [] and len(a.messages) == before
    assert a.ask("ещё раз") == "Нормальный ответ"
    assert [m["role"] for m in tools.load_memory()] == ["user", "assistant"]


def test_model_error_does_not_crash(make_agent):
    a = make_agent([TimeoutError("таймаут"), message("Готово")])
    out = a.ask("вопрос")
    assert "Ошибка обращения к модели" in out and "TimeoutError" in out
    assert tools.load_memory() == []
    assert a.ask("повтор") == "Готово"


def test_step_limit(make_agent):
    loop = [message(tool_calls=[tool_call("calculate", {"expression": "1+1"}, f"c{i}")])
            for i in range(agent_module.MAX_STEPS)]
    a = make_agent(loop)
    assert a.ask("зациклись") == agent_module.STEPS_ANSWER
    assert tools.load_memory() == []


def test_bad_tool_arguments_become_error_text(make_agent):
    bad = tool_call("calculate", {})
    bad.function.arguments = "{не json"
    a = make_agent([message(tool_calls=[bad]), message("Не получилось")])
    assert a.ask("?") == "Не получилось"
    assert [m for m in a.messages if m["role"] == "tool"][0]["content"].startswith("Ошибка calculate")


def test_context_is_restored_from_memory(make_agent):
    tools.save_memory([{"role": "user", "content": "А"}, {"role": "assistant", "content": "Б"}])
    a = agent_module.Agent()
    assert [m["role"] for m in a.messages] == ["system", "user", "assistant"]


def test_missing_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    import pytest
    with pytest.raises(RuntimeError):
        agent_module.Agent()
