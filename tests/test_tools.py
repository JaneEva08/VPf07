import pytest

from agent import tools


# ---------- файлы ----------
def test_file_roundtrip():
    tools.file_write("pi.json", "[3, 1, 4]")
    assert tools.file_read("pi.json") == "[3, 1, 4]"
    assert "pi.json" in tools.file_list()


@pytest.mark.parametrize("bad", ["../.env", "/etc/passwd", "a/../../x"])
def test_file_paths_outside_workspace_are_rejected(bad):
    with pytest.raises(ValueError):
        tools.file_read(bad)
    with pytest.raises(ValueError):
        tools.file_write(bad, "x")


# ---------- терминал ----------
@pytest.mark.parametrize("cmd", ["cat ../.env", "cat /etc/passwd", "ls /", "head -n 1 ../memory.json",
                                 "wc --files0-from=/etc/passwd"])
def test_run_command_cannot_leave_workspace(cmd, sandbox):
    out = tools.run_command(cmd)
    assert "SECRET" not in out
    assert "запрещ" in out.lower() or "только к папке" in out


def test_run_command_whitelist_and_python():
    assert "запрещена" in tools.run_command("rm -rf x")
    assert "запрещена" in tools.run_command("curl http://x")
    assert "только с --version" in tools.run_command("python -c 'print(1)'")
    assert "Менять системное время" in tools.run_command("date -s 2000-01-01")


def test_run_command_does_not_spawn_processes(monkeypatch):
    """Регресс: cat/wc/echo не существуют как программы в Windows — команды выполняются средствами Python."""
    import subprocess

    def boom(*a, **k):
        raise AssertionError("процесс запускать нельзя")
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    tools.file_write("a.txt", "one\ntwo\nthree\n")
    for cmd in ("ls", "pwd", "date", "whoami", "cat a.txt", "wc a.txt", "head -n 1 a.txt", "tail -n 1 a.txt",
                "python --version", "echo hi"):
        assert tools.run_command(cmd)


def test_run_command_head_tail_ls_date():
    tools.file_write("a.txt", "one\ntwo\nthree\n")
    assert tools.run_command("head -n 2 a.txt") == "one\ntwo"
    assert tools.run_command("tail -n 1 a.txt") == "three"
    assert tools.run_command("wc -w a.txt") == "3 a.txt"
    assert tools.run_command("ls") == "a.txt"
    assert len(tools.run_command("date +%Y")) == 4
    assert "не найден" in tools.call_tool("run_command", {"command": "cat nope.txt"})


def test_run_command_works_inside_workspace():
    tools.file_write("a.txt", "one\ntwo\n")
    assert tools.run_command("cat a.txt") == "one\ntwo"
    assert tools.run_command("wc -l a.txt").startswith("2")
    assert tools.run_command("echo привет") == "привет"


# ---------- калькулятор ----------
def test_calculate():
    assert tools.calculate("2**10 + 3*(4-1)") == "1033"
    assert tools.calculate("-7 // 2") == "-4"


@pytest.mark.parametrize("expr", ["__import__('os').system('ls')", "2**100000", "1/0", "abc", ""])
def test_calculate_rejects_bad_input(expr):
    with pytest.raises(Exception):
        tools.calculate(expr)


def test_call_tool_returns_errors_as_text():
    out = tools.call_tool("calculate", {"expression": "1/0"})
    assert out.startswith("Ошибка calculate")
    assert tools.call_tool("nope", {}).startswith("Ошибка nope")


# ---------- HTTP ----------
@pytest.mark.parametrize("url", ["http://127.0.0.1:8000/", "http://localhost/x", "http://169.254.169.254/latest",
                                 "file:///etc/passwd", "ftp://example.com"])
def test_http_blocks_local_and_non_http(url):
    with pytest.raises(ValueError):
        tools.http_get(url)
    with pytest.raises(ValueError):
        tools.http_post(url, {})


# ---------- погода, валюты, крипта (ответы API подменены) ----------
class Resp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_weather(monkeypatch):
    def fake_get(url, params=None, timeout=None, **kw):
        if "geocoding" in url:
            return Resp({"results": [{"latitude": 55.7, "longitude": 37.6, "name": "Москва", "country": "Россия"}]})
        return Resp({"current_weather": {"temperature": 7, "windspeed": 8, "weathercode": 53, "time": "2026-10-24T12:00"}})
    monkeypatch.setattr(tools.requests, "get", fake_get)
    out = tools.get_weather("Москва")
    assert "Москва" in out and "7°C" in out and "морось" in out


def test_weather_unknown_city(monkeypatch):
    monkeypatch.setattr(tools.requests, "get", lambda *a, **k: Resp({}))
    with pytest.raises(ValueError):
        tools.get_weather("Несуществующиград")


def test_crypto_price(monkeypatch):
    monkeypatch.setattr(tools.requests, "get", lambda *a, **k: Resp({"bitcoin": {"usd": 65000.5}}))
    assert tools.get_crypto_price("Bitcoin", "USD") == 65000.5
    with pytest.raises(ValueError):
        tools.get_crypto_price("litecoin", "usd")


def test_fiat_rate(monkeypatch):
    monkeypatch.setattr(tools.requests, "get", lambda *a, **k: Resp({"rates": {"RUB": 100.0}}))
    assert "250 EUR = 25000.0 RUB" in tools.get_fiat_rate("eur", "rub", 250)
    with pytest.raises(ValueError):
        tools.get_fiat_rate("eur", "xxx")
