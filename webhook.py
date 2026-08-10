"""Приём заявок снаружи.

Эндпоинт открыт всему интернету: его адрес попадёт в настройки чужой формы, тильды или
бота, а значит станет известен. Единственное, что отличает настоящую заявку от подделки —
подпись, посчитанная на общем секрете.

Считаем HMAC от **сырого тела запроса**, а не от разобранного JSON: словарь можно
сериализовать десятком способов, и подпись разойдётся на пустом месте.

От повторов защищает метка времени: перехваченный запрос нельзя переслать через час.
"""

import hashlib
import hmac
import os
import time

SECRET = os.environ.get("WEBHOOK_SECRET", "")
MAX_SKEW = int(os.environ.get("WEBHOOK_MAX_SKEW", 300))


class BadSignature(Exception):
    pass


def sign(body: bytes, timestamp: str, secret: str | None = None) -> str:
    """Подпись запроса. Той же функцией пользуется отправитель — и тесты."""
    payload = timestamp.encode() + b"." + body
    return hmac.new((secret or SECRET).encode(), payload, hashlib.sha256).hexdigest()


def check(body: bytes, timestamp: str | None, signature: str | None) -> None:
    if not SECRET:
        raise BadSignature("приём заявок не настроен")
    if not timestamp or not signature:
        raise BadSignature("нет подписи")

    try:
        sent_at = float(timestamp)
    except ValueError as e:
        raise BadSignature("метка времени не число") from e

    # Окно в обе стороны: часы отправителя могут убежать вперёд.
    if abs(time.time() - sent_at) > MAX_SKEW:
        raise BadSignature("запрос слишком старый")

    # Постоянное время сравнения: обычное == позволяет подобрать подпись по таймингу.
    if not hmac.compare_digest(sign(body, timestamp), signature):
        raise BadSignature("подпись не сходится")
