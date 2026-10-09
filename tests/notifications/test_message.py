"""Состав сообщения — ТЗ 8.4, критерии 17-20, решения Q-C4, Q-C11, Q-D10."""

from apps.monitoring.models import EventType
from apps.notifications.services.message import (
    ADDRESS_LIMIT,
    MAX_LENGTH,
    MessageInput,
    comment_of,
    compose,
)

URL = 'https://dns-monitoring.contoso.ru/domains/1/'


def message(**extra: object) -> MessageInput:
    defaults: dict[str, object] = {
        'event_type': EventType.NOT_ALLOWED.value,
        'domain_name': 'pay-gw.contoso.ru',
        'domain_url': URL,
        'changed': ('185.44.9.201',),
        'current': ('10.20.1.14', '10.20.2.61', '185.44.9.201'),
        'group_name': 'Платёжный контур',
    }
    return MessageInput(**(defaults | extra))  # type: ignore[arg-type]


def test_8_4_line_order_is_fixed() -> None:
    lines = compose(message(domain_comment='Звонить дежурному')).splitlines()

    assert lines[0] == '<b>Адрес вне разрешённых</b>'
    assert lines[1] == '<code>pay-gw.contoso.ru</code>'
    assert '185.44.9.201' in lines[2]
    assert lines[3].startswith('Актуальные адреса:')
    assert lines[4] == 'Группа: Платёжный контур'
    assert lines[5] == 'Звонить дежурному'
    assert 'Открыть домен' in lines[6]


def test_c11_headers_follow_the_approved_table() -> None:
    assert compose(message(event_type=EventType.BACK_TO_NORMAL.value)).startswith(
        '<b>Снова в норме</b>'
    )
    assert compose(message(event_type=EventType.RESOLVE_FAILED.value)).startswith(
        '<b>Домен не резолвится</b>'
    )


def test_18_group_comment_is_used_when_the_domain_has_none() -> None:
    assert comment_of(message(group_comment='Контур эквайринга')) == 'Контур эквайринга'
    assert (
        comment_of(message(domain_comment='Своё', group_comment='Группы')) == 'Своё'
    )


def test_19_no_comment_line_when_both_are_empty() -> None:
    text = compose(message())

    assert comment_of(message()) == ''
    # Строка просто отсутствует, а не выводится пустой.
    assert '\n\n' not in text


def test_c4_toggles_remove_their_lines() -> None:
    text = compose(
        message(domain_comment='Звонить дежурному'),
        include_current=False,
        include_comment=False,
        include_link=False,
    )

    assert 'Актуальные адреса' not in text
    assert 'Звонить дежурному' not in text
    assert 'Открыть домен' not in text


def test_20_long_address_list_is_shortened_and_the_comment_is_not() -> None:
    comment = 'Платёжный шлюз. ' * 120
    addresses = tuple(f'10.20.{index // 256}.{index % 256}' for index in range(30))

    text = compose(message(changed=addresses, current=addresses, domain_comment=comment))

    assert len(text) <= MAX_LENGTH
    assert f'и ещё {30 - ADDRESS_LIMIT}' in text
    # Комментарий не режется никогда (8.4).
    assert comment.strip() in text


def test_17_back_to_normal_carries_the_duration() -> None:
    text = compose(
        message(event_type=EventType.BACK_TO_NORMAL.value, duration='26 минут', changed=())
    )

    assert 'Проблема держалась 26 минут.' in text


def test_8_4_special_characters_are_escaped() -> None:
    text = compose(message(domain_comment='<b>срочно</b> & сразу'))

    assert '&lt;b&gt;срочно&lt;/b&gt; &amp; сразу' in text


def test_11_4_addresses_are_monospaced() -> None:
    text = compose(message())

    assert '<code>185.44.9.201</code>' in text
    assert '<code>10.20.1.14</code>' in text
