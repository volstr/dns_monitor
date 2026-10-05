"""Переменные окружения — ТЗ 11.10, критерии 53, 68, 77."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from config.env import EnvironmentConfigError, SmtpTls, django_database, load_env, parse_env

ROOT = Path(__file__).resolve().parents[2]
REQUIRED = ("DATABASE_URL", "REDIS_URL", "APP_BASE_URL", "SESSION_SECRET")
SMTP = {"SMTP_HOST": "smtp.example.com", "SMTP_FROM": "robot@example.com"}


def failed_variables(source: dict[str, str]) -> set[str]:
    return {error.variable for error in parse_env(source).errors}


def run_process(command: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        env={
            **env,
            "PATH": os.environ.get("PATH", ""),
            "DJANGO_SETTINGS_MODULE": "config.settings.prod",
        },
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_env_with_required_variables_is_valid(env: dict[str, str]) -> None:
    result = parse_env(env)

    assert result.errors == ()
    assert result.env is not None


@pytest.mark.parametrize("name", REQUIRED)
@pytest.mark.parametrize("state", ["absent", "empty"])
def test_53_missing_required_variable_is_named(env: dict[str, str], name: str, state: str) -> None:
    if state == "absent":
        del env[name]
    else:
        env[name] = "  "

    with pytest.raises(EnvironmentConfigError, match=name) as raised:
        load_env(env)

    assert {error.variable for error in raised.value.errors} == {name}


@pytest.mark.parametrize(
    "command",
    [
        pytest.param([sys.executable, "manage.py", "check"], id="manage.py"),
        pytest.param([sys.executable, "-c", "import config.wsgi"], id="wsgi"),
    ],
)
def test_53_process_does_not_start_without_variable(
    env: dict[str, str], command: list[str]
) -> None:
    del env["APP_BASE_URL"]

    completed = run_process(command, env)

    assert completed.returncode != 0
    assert "APP_BASE_URL" in completed.stderr


def test_53_process_starts_with_valid_env(env: dict[str, str]) -> None:
    # Контроль к тесту выше: процесс падает именно из-за переменной, а не по другой причине.
    completed = run_process([sys.executable, "manage.py", "check"], env)

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    "dsn",
    [
        "not-a-dsn",
        "https://sentry.example.com/42",
        "https://key@sentry.example.com/",
        "https://key@sentry.example.com/project",
        "ftp://key@sentry.example.com/42",
    ],
)
def test_68_invalid_sentry_dsn_is_named(env: dict[str, str], dsn: str) -> None:
    env["SENTRY_DSN"] = dsn

    with pytest.raises(EnvironmentConfigError, match="SENTRY_DSN"):
        load_env(env)
    assert failed_variables(env) == {"SENTRY_DSN"}


@pytest.mark.parametrize(
    "dsn",
    ["https://abc123@o1.ingest.sentry.io/4504", "https://key@sentry.example.com/prefix/42"],
)
def test_env_valid_sentry_dsn_is_accepted(env: dict[str, str], dsn: str) -> None:
    env["SENTRY_DSN"] = dsn

    assert failed_variables(env) == set()


POSTGRES_MATCHING_URL = {
    "POSTGRES_USER": "dns_monitoring",
    "POSTGRES_PASSWORD": "secret",
    "POSTGRES_DB": "dns_monitoring",
}


def test_env_postgres_account_matching_url_is_accepted(env: dict[str, str]) -> None:
    env |= POSTGRES_MATCHING_URL

    assert failed_variables(env) == set()


@pytest.mark.parametrize("variable", ["POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"])
def test_env_postgres_account_differing_from_url_is_named(
    env: dict[str, str], variable: str
) -> None:
    env |= POSTGRES_MATCHING_URL | {variable: "other"}

    with pytest.raises(EnvironmentConfigError, match=variable) as raised:
        load_env(env)
    assert failed_variables(env) == {variable}
    # Тексты ошибок не содержат значений: здесь это пароли.
    assert "other" not in str(raised.value)
    assert "secret" not in str(raised.value)


def test_env_postgres_password_is_compared_after_unescaping(env: dict[str, str]) -> None:
    env["DATABASE_URL"] = "postgresql://dns_monitoring:p%40ss%3Aw%2Frd@postgres:5432/dns_monitoring"
    env["POSTGRES_PASSWORD"] = "p@ss:w/rd"

    assert failed_variables(env) == set()


def test_env_postgres_account_is_not_checked_when_absent(env: dict[str, str]) -> None:
    # Внешняя база: POSTGRES_* нужны только сервису postgres из компоуза.
    assert "POSTGRES_PASSWORD" not in env

    assert failed_variables(env) == set()


def test_env_postgres_account_is_not_checked_against_invalid_url(env: dict[str, str]) -> None:
    env |= POSTGRES_MATCHING_URL | {"DATABASE_URL": "mysql://x"}

    assert failed_variables(env) == {"DATABASE_URL"}


def test_77_smtp_user_without_password_is_named(env: dict[str, str]) -> None:
    env |= SMTP | {"SMTP_USER": "robot"}

    with pytest.raises(EnvironmentConfigError, match="SMTP_PASSWORD"):
        load_env(env)
    assert failed_variables(env) == {"SMTP_PASSWORD"}


def test_env_smtp_password_without_user_is_error(env: dict[str, str]) -> None:
    env |= SMTP | {"SMTP_PASSWORD": "secret"}

    assert failed_variables(env) == {"SMTP_USER"}


def test_env_smtp_host_without_from_is_error(env: dict[str, str]) -> None:
    env["SMTP_HOST"] = "smtp.example.com"

    assert failed_variables(env) == {"SMTP_FROM"}


def test_env_smtp_consistency_is_not_checked_without_host(env: dict[str, str]) -> None:
    env["SMTP_USER"] = "robot"

    assert failed_variables(env) == set()


def test_env_complete_smtp_is_valid(env: dict[str, str]) -> None:
    env |= SMTP | {"SMTP_USER": "robot", "SMTP_PASSWORD": "secret", "SMTP_TLS": "tls"}

    result = parse_env(env)

    assert result.env is not None
    assert result.env.smtp_host == "smtp.example.com"
    assert result.env.smtp_tls is SmtpTls.TLS


def test_env_empty_optional_values_are_normal_mode(env: dict[str, str]) -> None:
    env |= {"SENTRY_DSN": "", "SMTP_HOST": "", "TELEGRAM_BOT_TOKEN": ""}

    result = parse_env(env)

    assert result.env is not None
    assert result.env.sentry_dsn is None
    assert result.env.smtp_host is None
    assert result.env.telegram_bot_token is None


def test_env_defaults_follow_table_11_10(env: dict[str, str]) -> None:
    result = parse_env(env)

    assert result.env is not None
    assert result.env.redis_db_state == 0
    assert result.env.redis_db_cache == 1
    assert result.env.auth_2fa_enabled is False
    assert result.env.dns_timeout_ms == 3000
    assert result.env.resolve_concurrency == 20
    assert result.env.smtp_port == 587
    assert result.env.smtp_tls is SmtpTls.STARTTLS
    assert result.env.sentry_environment == "production"
    assert result.env.sentry_traces_sample_rate == 0.0


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("DATABASE_URL", "mysql://user:secret@db:3306/dns"),
        ("DATABASE_URL", "postgresql://user:secret@db:5432"),
        ("DATABASE_URL", "postgresql://user:secret@db:port/dns"),
        ("REDIS_URL", "http://redis:6379"),
        ("REDIS_URL", "redis://redis:6379/2"),
        ("REDIS_URL", "redis://redis:6379?db=2"),
        ("REDIS_DB_STATE", "first"),
        ("REDIS_DB_CACHE", "16"),
        ("APP_BASE_URL", "localhost:8000"),
        ("APP_BASE_URL", "https://dns.example.com/monitoring"),
        ("SESSION_SECRET", "short"),
        ("AUTH_2FA_ENABLED", "yes"),
        ("AUTH_2FA_ENABLED", "True"),
        ("DNS_TIMEOUT_MS", "3s"),
        ("DNS_TIMEOUT_MS", "0"),
        ("DNS_TIMEOUT_MS", "1_000"),
        ("RESOLVE_CONCURRENCY", "-1"),
        ("SMTP_PORT", "70000"),
        ("SMTP_TLS", "ssl"),
        ("SMTP_FROM", "robot"),
        ("SENTRY_TRACES_SAMPLE_RATE", "1.5"),
        ("SENTRY_TRACES_SAMPLE_RATE", "nan"),
        ("TELEGRAM_BOT_TOKEN", "token"),
    ],
)
def test_env_format_error_is_named(env: dict[str, str], name: str, value: str) -> None:
    env[name] = value

    assert failed_variables(env) == {name}


def test_env_state_and_cache_db_must_differ(env: dict[str, str]) -> None:
    env |= {"REDIS_DB_STATE": "3", "REDIS_DB_CACHE": "3"}

    assert failed_variables(env) == {"REDIS_DB_CACHE"}


def test_env_invalid_db_number_does_not_report_false_collision(env: dict[str, str]) -> None:
    # Ошибочное значение подменяется значением по умолчанию; совпадение с ним — не отдельная ошибка.
    env |= {"REDIS_DB_STATE": "1", "REDIS_DB_CACHE": "abc"}

    assert failed_variables(env) == {"REDIS_DB_CACHE"}


def test_env_reports_all_errors_at_once(env: dict[str, str]) -> None:
    del env["DATABASE_URL"]
    env |= {"SMTP_TLS": "ssl", "DNS_TIMEOUT_MS": "fast"}

    assert failed_variables(env) == {"DATABASE_URL", "SMTP_TLS", "DNS_TIMEOUT_MS"}


def test_env_error_message_does_not_contain_values(env: dict[str, str]) -> None:
    env |= {
        "DATABASE_URL": "mysql://user:hunter2-password@db:3306/dns",
        "SESSION_SECRET": "short-secret-value",
    }

    with pytest.raises(EnvironmentConfigError) as raised:
        load_env(env)

    assert "hunter2-password" not in str(raised.value)
    assert "short-secret-value" not in str(raised.value)


def test_env_app_base_url_trailing_slash_is_stripped(env: dict[str, str]) -> None:
    env["APP_BASE_URL"] = "https://dns-monitoring.example.com/"

    result = parse_env(env)

    assert result.env is not None
    assert result.env.app_base_url == "https://dns-monitoring.example.com"


def test_django_database_is_built_from_url() -> None:
    database = django_database("postgresql://user:p%40ss@db:5433/dns?sslmode=require")

    assert database["ENGINE"] == "django.db.backends.postgresql"
    assert database["NAME"] == "dns"
    assert database["USER"] == "user"
    assert database["PASSWORD"] == "p@ss"
    assert database["HOST"] == "db"
    assert database["PORT"] == "5433"
    assert database["OPTIONS"] == {"sslmode": "require"}


def test_allowed_hosts_are_split_on_commas(env: dict[str, str]) -> None:
    env["ALLOWED_HOSTS"] = " dns.example.com ,10.20.0.7,  .internal "

    parsed = load_env(env)

    assert parsed.allowed_hosts == ("dns.example.com", "10.20.0.7", ".internal")


def test_allowed_hosts_may_be_absent(env: dict[str, str]) -> None:
    """Обязательность зависит от режима, поэтому разбор окружения её не требует."""
    del env["ALLOWED_HOSTS"]

    assert load_env(env).allowed_hosts == ()


def test_a_broken_host_name_is_named(env: dict[str, str]) -> None:
    env["ALLOWED_HOSTS"] = "dns.example.com, не хост"

    with pytest.raises(EnvironmentConfigError) as raised:
        load_env(env)

    assert {error.variable for error in raised.value.errors} == {"ALLOWED_HOSTS"}


def test_env_example_values_are_well_formed() -> None:
    """
    Значения в `.env.example` обязаны быть рабочими: README велит начинать с копирования файла.

    `SESSION_SECRET` подставляется здесь намеренно: в примере он оставлен пустым, чтобы никто не
    увёз заполнитель в бой, и заполнить его человек обязан сам — так написано и в файле, и в
    README. Проверяется всё остальное.

    Ломается такое молча. На момент написания теста `DATABASE_URL` был обрезан до
    `postgresql://dns_monitoring:dns_monitoring` — без хоста и базы, — и установка не поднималась
    вовсе, хотя копирование файла названо первым шагом.
    """
    source = {"SESSION_SECRET": "x" * 60}
    for raw in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            name, _, value = line.partition("=")
            if value.strip():
                source[name.strip()] = value.strip()

    result = parse_env(source)

    assert result.errors == (), [(e.variable, e.reason) for e in result.errors]
