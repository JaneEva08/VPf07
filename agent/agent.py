"""Логика агента: цикл «решаю — делаю — оцениваю» поверх OpenAI function calling."""
import json
import logging
import os

from dotenv import load_dotenv
from openai import OpenAI

from . import tools

load_dotenv()
logging.basicConfig(filename=str(tools.ROOT / "agent.log"), level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s", encoding="utf-8")
log = logging.getLogger("agent")

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
MAX_STEPS = 8          # защита от бесконечного цикла инструментов
CONTEXT_TURNS = 20     # сколько последних реплик подгружать из памяти
SYSTEM_PROMPT = (
    "Ты — AI-агент в терминале. Отвечай по-русски, понятно и структурированно. "
    "Сам выбирай инструмент: информация → web_search; погода → get_weather; "
    "цена криптовалюты → get_crypto_price; обычные валюты → get_fiat_rate; "
    "вычисления → calculate; файлы → file_read/file_write/file_list (только папка workspace/); "
    "действие в системе → run_command. Если данных не хватает (например, не указан город или "
    "валюта, и из контекста диалога это не ясно) — задай уточняющий вопрос. "
    "Важные факты о пользователе сохраняй через save_note. Не выдумывай данные — используй инструменты."
)


class Agent:
    def __init__(self):
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("Не задан OPENAI_API_KEY (см. .env.example)")
        self.client = OpenAI(api_key=key)
        saved = tools.load_memory()
        notes = [m["content"] for m in saved if m["role"] == "note"]
        turns = [m for m in saved if m["role"] in ("user", "assistant")][-CONTEXT_TURNS:]
        system = SYSTEM_PROMPT + (("\nЗаметки о пользователе: " + "; ".join(notes)) if notes else "")
        self.messages = [{"role": "system", "content": system}] + turns
        self.history = saved   # полная история, пишется в memory.json

    def ask(self, text: str) -> str:
        log.info("USER: %s", text)
        self.messages.append({"role": "user", "content": text})
        answer = "Не удалось получить ответ за отведённое число шагов."
        for step in range(MAX_STEPS):
            resp = self.client.chat.completions.create(
                model=MODEL, messages=self.messages, tools=tools.TOOL_SPECS)
            msg = resp.choices[0].message
            self.messages.append(msg.model_dump(exclude_none=True))
            if not msg.tool_calls:
                answer = msg.content or ""
                break
            for call in msg.tool_calls:
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                self.messages.append({"role": "tool", "tool_call_id": call.id,
                                      "content": tools.call_tool(call.function.name, args)})
        log.info("ANSWER: %s", answer)
        self.history += [{"role": "user", "content": text},
                         {"role": "assistant", "content": answer}]
        tools.save_memory(self.history)
        return answer


def run_agent():
    from colorama import Fore, init
    init()
    agent = Agent()
    print(Fore.CYAN + "AI-агент запущен. Напишите задачу (exit — выход, reset — очистить память)." + Fore.RESET)
    while True:
        try:
            text = input(Fore.GREEN + "Вы: " + Fore.RESET).strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text.lower() in {"exit", "quit", "выход"}:
            break
        if text.lower() == "reset":
            tools.save_memory([])
            agent = Agent()
            print("Память очищена.")
            continue
        if text:
            print(Fore.YELLOW + "Агент: " + Fore.RESET + agent.ask(text))
