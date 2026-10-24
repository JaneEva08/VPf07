"""Инструменты агента и их описания (JSON-схемы для function calling)."""
import ast
import json
import logging
import operator
import shlex
import subprocess
from pathlib import Path

import requests

log = logging.getLogger("agent.tools")

ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = ROOT / "workspace"          # песочница для файловых операций
WORKSPACE.mkdir(exist_ok=True)
MEMORY_PATH = ROOT / "memory.json"      # долговременная память
HTTP_TIMEOUT = 15
MAX_OUTPUT = 4000                       # режем длинные ответы инструментов


def _clip(text: str) -> str:
    return text if len(text) <= MAX_OUTPUT else text[:MAX_OUTPUT] + "\n...[обрезано]"


def _safe_path(name: str) -> Path:
    """Путь внутри workspace/; выход за пределы запрещён."""
    path = (WORKSPACE / name).resolve()
    if WORKSPACE.resolve() not in path.parents and path != WORKSPACE.resolve():
        raise ValueError("Доступ разрешён только к папке workspace/")
    return path


# ---------- 1. Веб-поиск ----------
def web_search(query: str, max_results: int = 5) -> str:
    from ddgs import DDGS
    results = DDGS().text(query, max_results=max_results)
    if not results:
        return "Ничего не найдено."
    return _clip("\n\n".join(
        f"{r.get('title')}\n{r.get('href')}\n{r.get('body')}" for r in results))


# ---------- 2. HTTP-запросы ----------
def http_get(url: str) -> str:
    r = requests.get(url, timeout=HTTP_TIMEOUT)
    return _clip(f"HTTP {r.status_code}\n{r.text}")


def http_post(url: str, json_body: dict | None = None) -> str:
    r = requests.post(url, json=json_body, timeout=HTTP_TIMEOUT)
    return _clip(f"HTTP {r.status_code}\n{r.text}")


# ---------- 3. Файлы ----------
def file_read(path: str) -> str:
    return _clip(_safe_path(path).read_text(encoding="utf-8"))


def file_write(path: str, content: str) -> str:
    p = _safe_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Записано {len(content)} символов в workspace/{path}"


def file_list() -> str:
    files = sorted(str(p.relative_to(WORKSPACE)) for p in WORKSPACE.rglob("*")
                   if p.is_file() and p.name != ".gitkeep")
    return "\n".join(files) or "workspace/ пуста"


# ---------- 4. Терминал (белый список, без shell) ----------
ALLOWED_COMMANDS = {"ls", "pwd", "date", "whoami", "echo", "cat", "head", "tail",
                    "wc", "python", "python3"}
ALLOWED_PY_ARGS = {"--version", "-V"}


def run_command(command: str) -> str:
    parts = shlex.split(command)
    if not parts or parts[0] not in ALLOWED_COMMANDS:
        return f"Команда запрещена. Разрешены: {', '.join(sorted(ALLOWED_COMMANDS))}"
    if parts[0].startswith("python") and not set(parts[1:]) <= ALLOWED_PY_ARGS:
        return "python разрешён только с --version"
    proc = subprocess.run(parts, cwd=WORKSPACE, capture_output=True, text=True, timeout=10)
    return _clip((proc.stdout + proc.stderr).strip() or "(пустой вывод)")


# ---------- 5. Память ----------
def load_memory() -> list[dict]:
    try:
        return json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_memory(history: list[dict]) -> None:
    MEMORY_PATH.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def save_note(note: str) -> str:
    """Агент сам решает, что стоит запомнить надолго."""
    history = load_memory()
    history.append({"role": "note", "content": note})
    save_memory(history)
    return "Заметка сохранена в memory.json"


# ---------- Погода ----------
WEATHER_CODES = {0: "ясно", 1: "преимущественно ясно", 2: "переменная облачность", 3: "пасмурно",
                 45: "туман", 48: "изморозь", 51: "морось", 61: "небольшой дождь", 63: "дождь",
                 65: "сильный дождь", 71: "небольшой снег", 73: "снег", 75: "сильный снег",
                 80: "ливень", 95: "гроза"}


def geocode_city(city: str) -> tuple[float, float, str]:
    r = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                     params={"name": city, "count": 1, "language": "ru"}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    found = r.json().get("results")
    if not found:
        raise ValueError(f"Город «{city}» не найден")
    c = found[0]
    return c["latitude"], c["longitude"], f"{c['name']}, {c.get('country', '')}"


def get_weather(city: str) -> str:
    lat, lon, label = geocode_city(city)
    r = requests.get("https://api.open-meteo.com/v1/forecast",
                     params={"latitude": lat, "longitude": lon, "current_weather": "true"},
                     timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    w = r.json()["current_weather"]
    cond = WEATHER_CODES.get(w.get("weathercode"), f"код {w.get('weathercode')}")
    return (f"{label}: {w['temperature']}°C, ветер {w['windspeed']} км/ч, {cond} "
            f"(на {w['time']})")


# ---------- Криптовалюта ----------
def get_crypto_price(coin: str, currency: str = "usd") -> float:
    coin, currency = coin.lower(), currency.lower()
    r = requests.get("https://api.coingecko.com/api/v3/simple/price",
                     params={"ids": coin, "vs_currencies": currency}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if coin not in data or currency not in data[coin]:
        raise ValueError(f"Нет данных по {coin}/{currency}")
    return float(data[coin][currency])


# ---------- Свой инструмент 1: курс обычных валют ----------
def get_fiat_rate(base: str, target: str, amount: float = 1.0) -> str:
    base, target = base.upper(), target.upper()
    r = requests.get(f"https://open.er-api.com/v6/latest/{base}", timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    rates = r.json().get("rates", {})
    if target not in rates:
        raise ValueError(f"Неизвестная валюта {target}")
    return f"{amount} {base} = {round(amount * rates[target], 4)} {target} (курс {rates[target]})"


# ---------- Свой инструмент 2: безопасный калькулятор ----------
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
        ast.FloorDiv: operator.floordiv, ast.USub: operator.neg, ast.UAdd: operator.pos}


def _eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 1000:
            raise ValueError("Слишком большая степень")
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    raise ValueError("Допустимы только числа и операции + - * / // % **")


def calculate(expression: str) -> str:
    return str(_eval(ast.parse(expression, mode="eval").body))


# ---------- Реестр и схемы ----------
def _spec(name, description, properties=None, required=None):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties or {},
                       "required": required or []}}}


_S = {"type": "string"}
TOOL_SPECS = [
    _spec("web_search", "Поиск информации в интернете (DuckDuckGo).",
          {"query": _S, "max_results": {"type": "integer"}}, ["query"]),
    _spec("http_get", "GET-запрос к внешнему API.", {"url": _S}, ["url"]),
    _spec("http_post", "POST-запрос к внешнему API с JSON-телом.",
          {"url": _S, "json_body": {"type": "object"}}, ["url"]),
    _spec("file_read", "Прочитать файл из папки workspace/.", {"path": _S}, ["path"]),
    _spec("file_write", "Записать (перезаписать) файл в папке workspace/.",
          {"path": _S, "content": _S}, ["path", "content"]),
    _spec("file_list", "Список файлов в workspace/."),
    _spec("run_command", "Выполнить безопасную терминальную команду (ls, pwd, date, cat, wc...).",
          {"command": _S}, ["command"]),
    _spec("save_note", "Запомнить важный факт о пользователе в долговременную память.",
          {"note": _S}, ["note"]),
    _spec("get_weather", "Текущая погода в городе (по названию).", {"city": _S}, ["city"]),
    _spec("get_crypto_price", "Цена криптовалюты. coin — id CoinGecko (bitcoin, ethereum, litecoin), "
          "currency — usd, eur, rub...", {"coin": _S, "currency": _S}, ["coin"]),
    _spec("get_fiat_rate", "Курс/конвертация обычных валют (USD, EUR, RUB...).",
          {"base": _S, "target": _S, "amount": {"type": "number"}}, ["base", "target"]),
    _spec("calculate", "Калькулятор: вычисляет арифметическое выражение, например 2**10 + 3*(4-1).",
          {"expression": _S}, ["expression"]),
]

TOOLS = {f.__name__: f for f in (
    web_search, http_get, http_post, file_read, file_write, file_list, run_command,
    save_note, get_weather, get_crypto_price, get_fiat_rate, calculate)}


def call_tool(name: str, arguments: dict) -> str:
    """Вызывает инструмент; ошибки возвращает модели текстом, чтобы она могла среагировать."""
    log.info("TOOL CALL %s %s", name, arguments)
    try:
        result = str(TOOLS[name](**arguments))
    except Exception as exc:  # noqa: BLE001 — ошибка уходит в лог и модели
        log.exception("TOOL ERROR %s", name)
        result = f"Ошибка {name}: {type(exc).__name__}: {exc}"
    log.info("TOOL RESULT %s: %s", name, result[:300])
    return result
