"""Провайдер ИИ: адрес, ключ и модель задаются переменными окружения (см. .env.example)."""
import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


def model_name() -> str:
    return os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def make_client() -> OpenAI:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("Не задан OPENAI_API_KEY (см. .env.example)")
    return OpenAI(api_key=key, base_url=os.getenv("OPENAI_BASE_URL") or None)
