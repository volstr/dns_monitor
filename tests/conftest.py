import pytest

# Только обязательные переменные 11.10: остальное должно брать значения по умолчанию.
REQUIRED_ENV: dict[str, str] = {
    "DATABASE_URL": "postgresql://dns_monitoring:secret@postgres:5432/dns_monitoring",
    "REDIS_URL": "redis://redis:6379",
    "APP_BASE_URL": "https://dns-monitoring.example.com",
    "SESSION_SECRET": "test-session-secret-" + "0123456789" * 4,
}


@pytest.fixture
def env() -> dict[str, str]:
    return dict(REQUIRED_ENV)


@pytest.fixture
def global_settings() -> object:
    """Строка общих настроек — ТЗ 4.1.

    Её создаёт миграция `catalog.0002_global_settings_row`, и обычным тестам она видна: они
    откатываются к состоянию после миграций. Но тест с `django_db(transaction=True)` вычищает
    таблицы целиком, и строка исчезает вместе с ними. Поэтому транзакционные тесты запрашивают
    эту фикстуру явно.
    """
    from apps.catalog.models import GlobalSettings

    row, _ = GlobalSettings.objects.get_or_create(pk=1)
    return row
