import pytest

# Минимум, с которым боевой процесс стартует: обязательные переменные 11.10 плюс
# ALLOWED_HOSTS, без которой `prod` не поднимается. Остальное должно брать умолчания.
REQUIRED_ENV: dict[str, str] = {
    'DATABASE_URL': 'postgresql://dns_monitoring:secret@postgres:5432/dns_monitoring',
    'REDIS_URL': 'redis://redis:6379',
    'APP_BASE_URL': 'https://dns-monitoring.example.com',
    'SESSION_SECRET': 'test-session-secret-' + '0123456789' * 4,
    'ALLOWED_HOSTS': 'dns-monitoring.example.com',
}


@pytest.fixture
def env() -> dict[str, str]:
    return dict(REQUIRED_ENV)


@pytest.fixture(autouse=True)
def clean_state_store() -> None:
    """
    Чистое состояние Redis перед каждым тестом.

    Ключи состояния переживают тестовую базу: кулдаун ручной проверки живёт тридцать секунд, и
    повторный прогон в ту же минуту падал на пустом месте. База здесь тестовая
    (`TEST_REDIS_DB_STATE`), а не рабочая, поэтому сброс безопасен — с рабочей так делать нельзя
    ни в коем случае (11.11: состояние не кэш).
    """
    import redis
    from django.conf import settings

    client = redis.Redis.from_url(settings.ENV.redis_url, db=settings.ENV.redis_db_state)
    client.flushdb()


@pytest.fixture
def global_settings() -> object:
    """
    Строка общих настроек — ТЗ 4.1.

    Её создаёт миграция `catalog.0002_global_settings_row`, и обычным тестам она видна: они
    откатываются к состоянию после миграций. Но тест с `django_db(transaction=True)` вычищает
    таблицы целиком, и строка исчезает вместе с ними. Поэтому транзакционные тесты запрашивают
    эту фикстуру явно.
    """
    from apps.catalog.models import GlobalSettings

    row, _ = GlobalSettings.objects.get_or_create(pk=1)
    return row
