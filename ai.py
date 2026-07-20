import os
import json
from openai import OpenAI
from pydantic import BaseModel
from typing import Literal
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ["OPENROUTER_API_KEY"],
)

MODEL = "nvidia/nemotron-3-super-120b-a12b:free"

SYSTEM = """Ты — ассистент отдела заявок малого бизнеса.
Тебе приходит сырой текст входящей заявки от клиента.
Верни ТОЛЬКО JSON-объект с полями:
- "topic": короткая тема заявки, 2-4 слова (например «хочет бота», «вопрос по цене», «спам»)
- "urgency": строго одно из "low", "medium", "high"
- "draft_reply": вежливый черновик ответа клиенту на русском, 1-2 предложения
Без пояснений, только JSON."""


class Markup(BaseModel):
    topic: str
    urgency: Literal["low", "medium", "high"]
    draft_reply: str


def analyze_lead(text: str) -> Markup:
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": text},
        ],
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content.strip()
    if content.startswith("```"):
        content = content.split("```")[1].removeprefix("json").strip()
    data = json.loads(content)
    return Markup.model_validate(data)


if __name__ == "__main__":
    result = analyze_lead("Здравствуйте, хочу бота для записи клиентов, срочно надо")
    print("Тема:     ", result.topic)
    print("Срочность:", result.urgency)
    print("Ответ:    ", result.draft_reply)
