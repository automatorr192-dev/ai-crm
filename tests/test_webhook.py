import json
import time

import pytest

import webhook


def headers(body: bytes, *, at: float | None = None, secret: str | None = None) -> dict:
    ts = str(at if at is not None else time.time())
    return {"X-Timestamp": ts, "X-Signature": webhook.sign(body, ts, secret)}


BODY = json.dumps({"text": "Хочу бота", "name": "Иван"}, ensure_ascii=False).encode()


def test_valid_signature_passes():
    head = headers(BODY)
    webhook.check(BODY, head["X-Timestamp"], head["X-Signature"])


def test_tampered_body_is_rejected():
    """Смысл подписи: тело нельзя подменить, не зная секрета."""
    head = headers(BODY)
    with pytest.raises(webhook.BadSignature):
        webhook.check(
            BODY.replace(b"\xd0\x98\xd0\xb2\xd0\xb0\xd0\xbd", b"Sam"),
            head["X-Timestamp"],
            head["X-Signature"],
        )


def test_foreign_secret_is_rejected():
    head = headers(BODY, secret="чужой")
    with pytest.raises(webhook.BadSignature):
        webhook.check(BODY, head["X-Timestamp"], head["X-Signature"])


def test_old_request_cannot_be_replayed():
    """Перехваченный запрос не должен работать вечно."""
    head = headers(BODY, at=time.time() - 3600)
    with pytest.raises(webhook.BadSignature):
        webhook.check(BODY, head["X-Timestamp"], head["X-Signature"])


def test_request_from_the_future_is_rejected():
    head = headers(BODY, at=time.time() + 3600)
    with pytest.raises(webhook.BadSignature):
        webhook.check(BODY, head["X-Timestamp"], head["X-Signature"])


def test_missing_headers_are_rejected():
    with pytest.raises(webhook.BadSignature):
        webhook.check(BODY, None, None)


def test_broken_timestamp_is_rejected():
    with pytest.raises(webhook.BadSignature):
        webhook.check(BODY, "вчера", "0" * 64)


def test_endpoint_is_closed_without_secret(monkeypatch):
    """Не настроен секрет — приём закрыт, а не открыт всем подряд."""
    monkeypatch.setattr(webhook, "SECRET", "")
    head = headers(BODY)
    with pytest.raises(webhook.BadSignature):
        webhook.check(BODY, head["X-Timestamp"], head["X-Signature"])


def test_signature_covers_the_raw_bytes():
    """Подписываем сырое тело: тот же словарь в другом порядке ключей — другая подпись."""
    one = json.dumps({"a": 1, "b": 2}).encode()
    two = json.dumps({"b": 2, "a": 1}).encode()
    assert webhook.sign(one, "1") != webhook.sign(two, "1")
