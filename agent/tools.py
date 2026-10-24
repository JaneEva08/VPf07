"""Инструменты агента и их описания (JSON-схемы для function calling)."""
import ast
import getpass
import ipaddress
import json
import logging
import operator
import shlex
import platform
import socket
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

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
def _check_url(url: str) -> None:
    """Только http(s) и только внешние адреса: localhost, частные сети и метаданные облака закрыты."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Разрешены только http:// и https:// адреса")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443)
    except socket.gaierror as exc:
        raise ValueError(f"Не удалось определить адрес {parsed.hostname}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError("Запросы к локальным и частным адресам запрещены")


def http_get(url: str) -> str:
    _check_url(url)
    r = requests.get(url, timeout=HTTP_TIMEOUT, allow_redirects=False)
    extra = f"\nПереадресация: {r.headers.get('Location')}" if r.is_redirect else ""
    return _clip(f"HTTP {r.status_code}{extra}\n{r.text}")


def http_post(url: str, json_body: dict | None = None) -> str:
    _check_url(url)
    r = requests.post(url, json=json_body, timeout=HTTP_TIMEOUT, allow_redirects=False)
    extra = f"\nПереадресация: {r.headers.get('Location')}" if r.is_redirect else ""
    return _clip(f"HTTP {r.status_code}{extra}\n{r.text}")


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


# ---------- 4. Терминал (белый список, без shell, только внутри workspace/) ----------
ALLOWED_COMMANDS = {"ls", "pwd", "date", "whoami", "echo", "cat", "head", "tail",
                    "wc", "python", "python3"}
ALLOWED_PY_ARGS = {"--version", "-V"}


def _check_command_args(parts: list[str]) -> str | None:
    """Возвращает текст отказа или None. Путь в аргументах — только внутри workspace/."""
    cmd, args = parts[0], parts[1:]
    if cmd.startswith("python"):
        return None if set(args) <= ALLOWED_PY_ARGS else "python разрешён только с --version"
    if cmd == "date" and any(a.startswith(("-s", "--set")) for a in args):
        return "Менять системное время запрещено"
    for arg in args:
        if arg.startswith("-"):
            if "/" in arg or ".." in arg:          # --files0-from=/etc/passwd и подобное
                return "Пути в параметрах запрещены"
            continue
        if cmd in {"echo", "date"}:                # текст и формат даты — не пути
            continue
        try:
            _safe_path(arg)
        except ValueError:
            return "Доступ разрешён только к папке workspace/"
    return None


def _flags_and_args(args: list[str]) -> tuple[list[str], list[str]]:
    return [a for a in args if a.startswith("-")], [a for a in args if not a.startswith("-")]


def _read(name: str) -> str:
    path = _safe_path(name)
    if not path.is_file():
        raise FileNotFoundError(f"Файл не найден: {name}")
    return path.read_text(encoding="utf-8", errors="replace")


def _lines_count(args: list[str], default: int = 10) -> tuple[int, list[str]]:
    """Разбор `-n N` для head/tail; возвращает (N, остальные аргументы)."""
    rest, n, i = [], default, 0
    while i < len(args):
        if args[i] == "-n" and i + 1 < len(args):
            n, i = int(args[i + 1]), i + 2
        elif args[i].startswith("-n") and args[i][2:].isdigit():
            n, i = int(args[i][2:]), i + 1
        else:
            rest.append(args[i])
            i += 1
    return max(n, 0), rest


def _head_tail(args, head: bool) -> str:
    n, rest = _lines_count(args)
    chunks = []
    for name in (r for r in rest if not r.startswith("-")):
        lines = _read(name).splitlines()
        chunks.append("\n".join(lines[:n] if head else (lines[-n:] if n else [])))
    return "\n".join(chunks)


def _ls(args):
    _, paths = _flags_and_args(args)
    out = []
    for name in paths or ["."]:
        target = _safe_path(name)
        if target.is_dir():
            out += sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir() if p.name != ".gitkeep")
        elif target.exists():
            out.append(name)
        else:
            raise FileNotFoundError(f"Не найдено: {name}")
    return "\n".join(out)


def _wc(args):
    flags, names = _flags_and_args(args)
    rows = []
    for name in names:
        text = _read(name)
        counts = {"-l": text.count("\n"), "-w": len(text.split()), "-c": len(text.encode("utf-8"))}
        chosen = [counts[f] for f in ("-l", "-w", "-c") if f in flags] or list(counts.values())
        rows.append(" ".join(str(c) for c in chosen) + f" {name}")
    return "\n".join(rows)


def _date(args):
    now = datetime.now()
    fmt = next((a[1:] for a in args if a.startswith("+")), None)
    return now.strftime(fmt) if fmt else now.isoformat(timespec="seconds")


COMMANDS = {
    "pwd": lambda a: "workspace/",
    "ls": _ls,
    "date": _date,
    "whoami": lambda a: getpass.getuser(),
    "echo": lambda a: " ".join(x for x in a if x != "-n"),
    "cat": lambda a: "\n".join(_read(n) for n in _flags_and_args(a)[1]),
    "head": lambda a: _head_tail(a, head=True),
    "tail": lambda a: _head_tail(a, head=False),
    "wc": _wc,
    "python": lambda a: "Python " + platform.python_version(),
    "python3": lambda a: "Python " + platform.python_version(),
}


def run_command(command: str) -> str:
    """Команды из белого списка выполняются средствами Python, без запуска процессов
    и без shell: одинаково работает в Windows и Linux, наружу из workspace/ не выйти."""
    parts = shlex.split(command)
    if not parts or parts[0] not in ALLOWED_COMMANDS:
        return f"Команда запрещена. Разрешены: {', '.join(sorted(ALLOWED_COMMANDS))}"
    refusal = _check_command_args(parts)
    if refusal:
        return refusal
    return _clip(COMMANDS[parts[0]](parts[1:]).strip() or "(пустой вывод)")


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
                 45: "туман", 48: "изморозь", 51: "слабая морось", 53: "морось", 55: "сильная морось",
                 56: "ледяная морось", 57: "сильная ледяная морось", 61: "небольшой дождь",
                 63: "дождь", 65: "сильный дождь", 66: "ледяной дождь", 67: "сильный ледяной дождь",
                 71: "небольшой снег", 73: "снег", 75: "сильный снег", 77: "снежные зёрна",
                 80: "небольшой ливень", 81: "ливень", 82: "сильный ливень", 85: "снегопад",
                 86: "сильный снегопад", 95: "гроза", 96: "гроза с градом", 99: "сильная гроза с градом"}


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
