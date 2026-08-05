import logging

from ai import analyze_lead
from db import add_lead, get_all_leads, init_db


def intake(client_name: str, client_contact: str, text: str):
    """Заявка сохраняется в любом случае: разметка — приятное дополнение, а потерянный
    лид — потерянные деньги."""
    try:
        markup = analyze_lead(text)
    except RuntimeError as e:
        logging.warning("заявка от %s сохранена без разметки: %s", client_name, e)
        add_lead(client_name, client_contact, text)
        return
    add_lead(client_name, client_contact, text, markup.topic, markup.urgency, markup.draft_reply)
    print(f"Заявка от {client_name} обработана и сохранена")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    intake("Иван", "@ivan", "Здравствуйте, хочу бота для записи клиентов, срочно надо")
    intake("Мария", "@maria", "а сколько стоит и есть ли рассрочка?")
    print("\n--- Все заявки в базе ---")
    for lead in get_all_leads():
        print(lead)
