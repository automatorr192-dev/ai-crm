import sqlite3

DB_PATH = "crm.db"


def get_connection():
    return sqlite3.connect(DB_PATH)


def init_db():
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            id            INTEGER PRIMARY KEY,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            client_name   TEXT,
            client_contact TEXT,
            text          TEXT,
            topic         TEXT,
            urgency       TEXT,
            draft_reply   TEXT,
            status        TEXT DEFAULT 'new'
        )
    """)
    conn.commit()
    conn.close()
    print("База готова: таблица leads создана")


def add_lead(client_name, client_contact, text, topic=None, urgency=None, draft_reply=None):
    conn = get_connection()
    conn.execute(
        "INSERT INTO leads (client_name, client_contact, text, topic, urgency, draft_reply) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (client_name, client_contact, text, topic, urgency, draft_reply),
    )
    conn.commit()
    conn.close()


def get_all_leads():
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM leads ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(row) for row in rows]


if __name__ == "__main__":
    init_db()
    add_lead(
        client_name="Иван",
        client_contact="@ivan",
        text="Здравствуйте, хочу бота для записи клиентов, срочно надо",
    )
    for lead in get_all_leads():
        print(lead)
