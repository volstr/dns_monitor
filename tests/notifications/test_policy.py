"""Уведомлять ли о событии — ТЗ 8.1, критерии 14 и 15, решения Q-D7 и Q-D8."""

import pytest

from apps.catalog.models import NotificationMode
from apps.monitoring.models import EventType
from apps.notifications.services.policy import Send, Skip, SkipReason, decide

OPENING = "42"


def result(
    event_type: EventType,
    mode: NotificationMode,
    *,
    back_to_normal: bool = True,
    opening: str | None = OPENING,
) -> Send | Skip:
    return decide(
        event_type=event_type.value,
        mode=mode.value,
        notify_back_to_normal=back_to_normal,
        opening_message_id=opening,
    )


def test_14_mode_none_sends_nothing() -> None:
    # Критерий 14: журнал заполняется, сообщения не уходят.
    for event_type in EventType:
        assert isinstance(result(event_type, NotificationMode.NONE), Skip)


def test_15_address_change_mode_ignores_not_allowed() -> None:
    assert result(EventType.NOT_ALLOWED, NotificationMode.ADDRESS_CHANGE) == Skip(
        reason=SkipReason.MODE
    )
    assert isinstance(result(EventType.ADDRESS_APPEARED, NotificationMode.ADDRESS_CHANGE), Send)


def test_8_1_not_allowed_mode_ignores_address_changes() -> None:
    assert result(EventType.ADDRESS_APPEARED, NotificationMode.NOT_ALLOWED) == Skip(
        reason=SkipReason.MODE
    )
    assert isinstance(result(EventType.NOT_ALLOWED, NotificationMode.NOT_ALLOWED), Send)


@pytest.mark.parametrize(
    "mode", [NotificationMode.ADDRESS_CHANGE, NotificationMode.NOT_ALLOWED]
)
def test_8_1_resolve_failed_notifies_in_every_mode_but_none(mode: NotificationMode) -> None:
    assert isinstance(result(EventType.RESOLVE_FAILED, mode), Send)


@pytest.mark.parametrize(
    "mode", [NotificationMode.ADDRESS_CHANGE, NotificationMode.NOT_ALLOWED]
)
def test_d8_resolve_recovered_follows_resolve_failed(mode: NotificationMode) -> None:
    decision = result(EventType.RESOLVE_RECOVERED, mode)

    assert decision == Send(reply_to=OPENING)


def test_d8_recovery_is_not_governed_by_the_back_to_normal_flag() -> None:
    # Флаг про адрес, снова ставший совпавшим, а не про то, что домен снова резолвится.
    decision = result(
        EventType.RESOLVE_RECOVERED, NotificationMode.NOT_ALLOWED, back_to_normal=False
    )

    assert isinstance(decision, Send)


def test_8_1_back_to_normal_obeys_its_flag() -> None:
    assert isinstance(result(EventType.BACK_TO_NORMAL, NotificationMode.NOT_ALLOWED), Send)
    assert result(
        EventType.BACK_TO_NORMAL, NotificationMode.NOT_ALLOWED, back_to_normal=False
    ) == Skip(reason=SkipReason.BACK_TO_NORMAL_DISABLED)


def test_8_1_back_to_normal_notifies_in_address_change_mode_too() -> None:
    # «Уведомляет дополнительно… и только в режимах, отличных от „Не уведомлять"».
    assert isinstance(result(EventType.BACK_TO_NORMAL, NotificationMode.ADDRESS_CHANGE), Send)


def test_d7_continuation_without_an_opening_message_is_skipped() -> None:
    for event_type in (EventType.BACK_TO_NORMAL, EventType.RESOLVE_RECOVERED):
        assert result(event_type, NotificationMode.NOT_ALLOWED, opening=None) == Skip(
            reason=SkipReason.NO_OPENING_MESSAGE
        )


def test_d7_continuation_answers_into_the_thread() -> None:
    assert result(EventType.BACK_TO_NORMAL, NotificationMode.NOT_ALLOWED) == Send(reply_to=OPENING)


def test_7_4_watch_started_never_notifies() -> None:
    for mode in NotificationMode:
        assert result(EventType.WATCH_STARTED, mode) == Skip(reason=SkipReason.NEVER)


def test_opening_message_is_not_used_as_a_thread_for_new_alerts() -> None:
    # NOT_ALLOWED открывает ветку, а не отвечает в неё.
    assert result(EventType.NOT_ALLOWED, NotificationMode.NOT_ALLOWED) == Send(reply_to=None)
