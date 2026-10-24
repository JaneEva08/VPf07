# AI-агент (ДЗ VPf07)

Терминальный AI-агент на OpenAI function calling: модель сама выбирает инструмент, вызывает его и формирует ответ.

## Инструменты
| Инструмент | Что делает |
|---|---|
| `web_search` | поиск в интернете (DuckDuckGo, пакет `ddgs`) |
| `http_get` / `http_post` | запросы к внешним API; только http(s), локальные и частные адреса закрыты, переадресации не выполняются |
| `file_read` / `file_write` / `file_list` | файлы, только внутри `workspace/` |
| `run_command` | команды из белого списка (ls, pwd, date, whoami, cat, head, tail, wc, echo, python --version) выполняются средствами Python, без запуска процессов и shell; пути в аргументах — только внутри `workspace/` |
| `get_weather` | текущая погода по названию города (Open-Meteo: геокодинг + прогноз) |
| `get_crypto_price` | курс криптовалюты (CoinGecko) |
| `get_fiat_rate` | **свой #1:** курс/конвертация обычных валют (USD/EUR/RUB…) |
| `calculate` | **свой #2:** безопасный калькулятор (через AST, без `eval`) |
| `save_note` | запомнить факт надолго |

Память: каждая реплика пользователя и агента сохраняется в `memory.json`; при старте подгружаются последние 20 реплик и все заметки. Команда `reset` очищает память. Неудачный ход (ошибка API, пустой ответ, превышен лимит шагов) в память не попадает.
Логи всех шагов (запросы, вызовы инструментов, ошибки) — в `agent.log`.

## Запуск
```bash
python -m venv venv && venv\Scripts\activate     # Linux/macOS: source venv/bin/activate
python -m pip install -r requirements.txt
copy .env.example .env      # впишите OPENAI_API_KEY (и при необходимости OPENAI_BASE_URL, OPENAI_MODEL)
python run.py
```

## Тесты
```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q          # без сети и без ключей, API подменены
```

## Примеры запросов
- «Привет. Что ты умеешь?»
- «Найди информацию про Москву, какое самое известное место?»
- «Сколько сейчас стоит 1 bitcoin в usd?» → «А litecoin?» (контекст из памяти)
- «Какая сейчас погода в Москве?»
- «Создай файл pi.json и сохрани туда первые 100 цифр числа Пи»
- «Переведи 250 EUR в RUB», «Посчитай 2**20 / 3»

## Структура
```
agent/agent.py   цикл агента, память, обработка ошибок модели
agent/provider.py клиент и модель из переменных окружения
agent/tools.py   инструменты и их JSON-схемы
run.py           запуск из терминала
tests/           pytest, без сети
workspace/       песочница для файлов агента (содержимое не в git)
```
