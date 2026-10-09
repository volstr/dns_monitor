"""
Разбор белого списка — ТЗ 10.2, 13.3; критерии 44, 45.

Проверяются причины словами, а не коды: 10.2 требует объяснять человеку, что именно не так.
"""

import pytest

from apps.catalog.services.allowlist import parse_allowlist
from core.types.dns import RecordType


def only(text: str, record_types: tuple[str, ...] = ()) -> object:
    parsed = parse_allowlist(text, record_types)
    assert len(parsed.lines) == 1
    return parsed.lines[0]


def test_44_bad_octet_is_named_in_plain_words() -> None:
    """Критерий 44: строка подсвечена, причина показана, сохранение заблокировано."""
    parsed = parse_allowlist('10.20.999.0/24')

    assert parsed.can_save is False
    assert parsed.errors[0].number == 1
    assert parsed.errors[0].error == '999 — недопустимый октет. Значение каждого октета от 0 до 255'


def test_44_correct_lines_survive_a_broken_one() -> None:
    """10.2: «корректные строки при этом не теряются»."""
    parsed = parse_allowlist('10.20.0.0/22\n10.20.999.0/24\n10.30.4.0/24')

    assert parsed.can_save is False
    assert [line.number for line in parsed.errors] == [2]
    assert [str(line.network) for line in parsed.lines if line.network] == [
        '10.20.0.0/22',
        '10.30.4.0/24',
    ]


def test_44_mask_out_of_range_is_explained() -> None:
    assert only('10.20.0.0/33').error == 'длина маски у IPv4 — от 0 до 32, а здесь 33'


def test_44_mask_must_be_a_number() -> None:
    assert only('10.20.0.0/abc').error == 'длина маски записывается числом после косой черты'


def test_44_wrong_octet_count_is_explained() -> None:
    assert only('10.20.1').error == 'в IPv4-адресе четыре октета, а здесь 3'


def test_44_letters_instead_of_an_octet() -> None:
    assert only('10.20.x.1').error == '«x» — не число. Октет записывается цифрами'


def test_44_garbage_is_not_an_address() -> None:
    assert only('что-то не то').error is not None


@pytest.mark.parametrize('text', ['10.20.3.7', '10.20.0.0/22', '2a02:6b8::1', '2a02:6b8::/32'])
def test_10_2_valid_entries_are_accepted(text: str) -> None:
    line = only(text)

    assert line.error is None
    assert line.network is not None


def test_6_3_address_without_a_mask_keeps_its_text() -> None:
    """6.3: запись показывается как ввёл пользователь — «10.20.3.7», а не «10.20.3.7/32»."""
    line = only('10.20.3.7')

    assert line.raw == '10.20.3.7'
    assert str(line.network) == '10.20.3.7/32'
    assert line.normalized_from is None


def test_13_3_host_bits_are_normalised_and_the_change_is_shown() -> None:
    """Q-13.3: нормализовать с показом изменения, а не молча и не отказом."""
    line = only('10.20.1.5/24')

    assert line.error is None
    assert str(line.network) == '10.20.1.0/24'
    assert line.normalized_from == '10.20.1.5/24'
    assert line.raw == '10.20.1.0/24'


def test_10_2_empty_lines_are_not_an_error() -> None:
    parsed = parse_allowlist('10.20.0.0/22\n\n\n10.30.4.0/24\n')

    assert parsed.can_save is True
    assert len(parsed.lines) == 2
    # Нумерация остаётся настоящей: человек ищет строку глазами в своём тексте.
    assert [line.number for line in parsed.lines] == [1, 4]


def test_45_entry_covered_by_another_warns_but_saves() -> None:
    """Критерий 45: предупреждение о перекрытии, сохранение разрешено."""
    parsed = parse_allowlist('10.20.0.0/22\n10.20.3.7')

    assert parsed.can_save is True
    warning = parsed.warnings[0]
    assert warning.number == 2
    assert warning.warning == (
        '10.20.3.7 уже входит в 10.20.0.0/22 из строки 1 — запись, скорее всего, лишняя'
    )


def test_10_2_duplicate_warns_and_is_stored_once() -> None:
    parsed = parse_allowlist('10.20.0.0/22\n10.20.0.0/22')

    assert parsed.can_save is True
    assert parsed.warnings[0].warning == 'дубликат строки 1'
    assert len(parsed.entries) == 1


def test_10_2_entry_of_a_family_the_domain_does_not_ask_for_warns() -> None:
    parsed = parse_allowlist('2a02:6b8::/32', record_types=(RecordType.A.value,))

    assert parsed.can_save is True
    assert parsed.warnings[0].warning == (
        'тип записи aaaa у домена выключен — эта запись никогда не совпадёт'
    )


def test_10_2_family_warning_is_silent_when_the_type_is_on() -> None:
    parsed = parse_allowlist(
        '2a02:6b8::/32', record_types=(RecordType.A.value, RecordType.AAAA.value)
    )

    assert parsed.warnings == ()


def test_10_2_overly_long_line_is_refused() -> None:
    assert only('1' * 60).error == 'запись длиннее, чем может быть адрес с маской'
