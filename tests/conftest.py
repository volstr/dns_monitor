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
