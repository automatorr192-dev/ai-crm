"""Логи и падения.

Две разные задачи, поэтому два инструмента.

structlog: событие пишется не строкой, а полями. Строку «разбор: 5 находок, 1.20 ₽» глазами
прочесть можно, а спросить у неё «покажи все разборы дороже 10 ₽» — нельзя. Локально
включается человекочитаемый вывод с цветом, в облаке — JSON, который умеет читать любой
сборщик логов.

Sentry: падение должно найти нас само. Без него трейсбек уедет в логи контейнера, и мы
узнаем о поломке от пользователя — а на логи Amvera мы и так решили не полагаться.
Пустой SENTRY_DSN отключает всё целиком, поэтому локально ничего настраивать не нужно.
"""

import logging
import os
import sys

import structlog

log = structlog.get_logger()

_configured = False


def _pretty() -> bool:
    """В терминал — читаемо, в облако — JSON. Определяем по наличию живой консоли."""
    return sys.stderr.isatty()


def setup() -> None:
    # Бот и веб живут в одном процессе и оба зовут setup(): второй раз пере-инициализировать
    # Sentry незачем.
    global _configured
    if _configured:
        return
    _configured = True

    logging.basicConfig(format="%(message)s", stream=sys.stderr, level=logging.INFO)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.dev.ConsoleRenderer() if _pretty() else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=True,
    )

    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        log.info("sentry.off", reason="SENTRY_DSN пуст")
        return

    import sentry_sdk

    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("SENTRY_ENV", "prod"),
        # Договоры содержат персональные данные, а тексты запросов к модели — чужие
        # документы целиком. В трекер ошибок им нельзя ни при каких настройках.
        send_default_pii=False,
        traces_sample_rate=0,
    )
    log.info("sentry.on")
