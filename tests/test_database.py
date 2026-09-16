import pytest
from django.db import connection


@pytest.mark.django_db
def test_database_is_postgresql_16() -> None:
    assert connection.vendor == "postgresql"
    assert connection.pg_version // 10_000 == 16
