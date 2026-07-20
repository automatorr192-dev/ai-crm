from db import init_db, add_lead, get_all_leads
from ai import analyze_lead


def intake(client_name: str, client_contact: str, text: str):
    markup = analyze_lead(text)
    add_lead(client_name, client_contact, text, markup.topic, markup.urgency, markup.draft_reply)
    print(f"Заявка от {client_name} обработана и сохранена")


if __name__ == "__main__":
    init_db()
    intake("Иван", "@ivan", "Здравствуйте, хочу бота для записи клиентов, срочно надо")
    intake("Мария", "@maria", "а сколько стоит и есть ли рассрочка?")
    print("\n--- Все заявки в базе ---")
    for lead in get_all_leads():
        print(lead)
