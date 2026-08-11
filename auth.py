"""Пароли и сессии.

Раньше вход был один на всех: логин и пароль лежали в переменных окружения. В CRM,
где у сделки есть ответственный, это перестаёт работать — «кто перевёл клиента в отказ»
остаётся без ответа, если все входят под одним именем.

Ничего постороннего для этого не нужно: хеш пароля берётся из stdlib, сессия — это
подписанная строка в cookie. Серверу не приходится хранить список активных сессий,
а значит перезапуск не выкидывает всех пользователей.
"""

import base64
import hashlib
import hmac
import os
import secrets
import time

# scrypt подобран так, чтобы проверка пароля занимала заметное для перебора время
# и незаметное для человека. Числа стандартные, менять их — менять формат хеша.
_N, _R, _P = 2**14, 8, 1
_SALT_BYTES = 16

SESSION_COOKIE = "crm_session"
SESSION_DAYS = int(os.environ.get("SESSION_DAYS", 14))


def _secret() -> str:
    """Ключ подписи сессий.

    Пустой ключ означал бы, что cookie может подделать кто угодно, поэтому в этом случае
    берём случайный на время жизни процесса: вход продолжит работать, но переживёт
    перезапуск только там, где ключ задан осознанно.
    """
    return os.environ.get("SESSION_SECRET") or _fallback


_fallback = secrets.token_hex(32)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, n, r, p, salt_hex, digest_hex = stored.split("$")
        if algorithm != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p)
        )
    except (ValueError, TypeError):
        return False
    # Постоянное время сравнения: обычное == подсказывает подбирающему, сколько символов
    # он уже угадал, разницей во времени ответа.
    return hmac.compare_digest(digest.hex(), digest_hex)


def _sign(payload: str) -> str:
    mac = hmac.new(_secret().encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def make_session(user_id: int) -> str:
    """Cookie вида «кто.до-какого-момента.подпись».

    Срок годности лежит внутри подписи, поэтому продлить сессию, поправив cookie в
    браузере, не выйдет: подпись перестанет сходиться.
    """
    expires = int(time.time()) + SESSION_DAYS * 86400
    payload = f"{user_id}.{expires}"
    return f"{payload}.{_sign(payload)}"


def read_session(cookie: str | None) -> int | None:
    if not cookie:
        return None
    try:
        user_id, expires, signature = cookie.split(".")
        payload = f"{user_id}.{expires}"
    except ValueError:
        return None

    # Сравниваем байты: compare_digest на строке с не-ASCII падает TypeError, а подпись
    # в cookie пишет кто угодно, в том числе кириллицей.
    if not hmac.compare_digest(_sign(payload).encode(), signature.encode()):
        return None
    if int(expires) < time.time():
        return None
    return int(user_id)
