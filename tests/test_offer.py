"""Черновик КП: текст пишет модель, деньги по этапам считает код из суммы сделки."""

from decimal import Decimal

import pytest

import db
import insight
from ai import OfferDraft, OfferLine

DRAFT = OfferDraft(
    title="Заявки салона в одном месте",
    task="Записи теряются в директе, администратор отвечает вечером.",
    solution="Соберём заявки из всех каналов в одну ленту и напомним клиенту о визите.",
    lines=[
        OfferLine(name="Разбор процесса записи", share=20),
        OfferLine(name="Бот записи и напоминаний", share=50),
        OfferLine(name="Запуск и обучение", share=30),
    ],
    result=["Ни одна заявка не теряется", "Напоминания уходят сами"],
)


@pytest.fixture
def model(monkeypatch):
    seen = []

    def fake(deal):
        seen.append(deal)
        return DRAFT

    monkeypatch.setattr(insight, "write_offer", fake)
    return seen


async def deal(amount=None):
    lead, _ = await db.add_lead("Полина", "@polina", "Хочу бота для записи в салон")
    if amount is not None:
        await db.set_amount(lead.id, Decimal(amount))
    return lead


@pytest.mark.parametrize(
    ("amount", "shares", "prices"),
    [
        (100000, [20, 50, 30], [20000, 50000, 30000]),
        (100000, [33, 33, 34], [33000, 33000, 34000]),
        (95500, [1, 1, 1], [31800, 31800, 31900]),
        (60000, [2, 1], [40000, 20000]),
        (None, [50, 50], [0, 0]),
    ],
)
def test_price_split_always_adds_up(amount, shares, prices):
    result = insight.split_price(amount, shares)

    assert result == prices
    if amount:
        assert sum(result) == amount


async def test_draft_is_saved_and_priced_from_the_deal(database, manager, model):
    lead = await deal(amount=80000)

    response = manager.post(f"/leads/{lead.id}/offer/draft", follow_redirects=False)

    assert response.headers["location"] == f"/leads/{lead.id}/offer"
    page = manager.get(f"/leads/{lead.id}/offer").text
    assert "Заявки салона в одном месте" in page
    assert "Бот записи и напоминаний" in page
    assert "40 000" in page and "16 000" in page and "24 000" in page
    assert "Напоминания уходят сами" in page
    assert "Черновик написала модель" in page
    assert "Хочу бота для записи в салон" in model[0]


async def test_failure_keeps_the_plain_offer(database, manager, monkeypatch):
    lead = await deal(amount=80000)

    def broken(deal):
        raise RuntimeError("все модели лежат")

    monkeypatch.setattr(insight, "write_offer", broken)

    response = manager.post(f"/leads/{lead.id}/offer/draft", follow_redirects=False)

    assert response.headers["location"].endswith("?draft=0")
    page = manager.get(response.headers["location"]).text
    assert "Модель не ответила" in page
    assert "80 000" in page


async def test_viewer_cannot_draft(database, watcher, model):
    lead = await deal()

    assert watcher.post(f"/leads/{lead.id}/offer/draft", follow_redirects=False).status_code == 403
    assert model == []


def test_offer_needs_at_least_one_line():
    with pytest.raises(ValueError):
        OfferDraft(title="т", task="з", solution="р", lines=[])
