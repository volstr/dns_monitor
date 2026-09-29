"""
След реестра для графика — ТЗ 9.2, Q-E1.

График показывает «след из отметок по времени, а не сплошную полосу»: корзина закрашена, если
адрес отдавался хотя бы в одном ответе за её период. Здесь готовятся суточные корзины и ось;
сколько отметок поместится и как их укрупнить, решает сам виджет — по 9.2 число корзин
определяется шириной элемента, а не календарём.

Ось по умолчанию охватывает **весь срок наблюдения за доменом**, чтобы след любого адреса
гарантированно попал в поле зрения и не требовались пометки «интервал начался левее».
"""

from __future__ import annotations

from datetime import date, datetime

from apps.monitoring.models import AddressObservationBucket, BucketSize
from core.types.widgets import TraceData, TraceRow


def trace_of_domain(
    *, domain_id: int, at: datetime, statuses: dict[str, str]
) -> TraceData:
    """
    Суточные следы адресов домена.

    ``statuses`` — текущий статус каждого адреса: по 9.2 цвет отметки берётся из **текущего**
    статуса, а не из того, каким он был в тот день.

    Адрес, которого в ``statuses`` нет, в след не попадает. Это не мелочь: при снятом общем
    флаге IPv6 такие адреса «не существуют» (Q-D27), и в реестре их нет. В корзинах наблюдений
    они при этом остаются — их записали, пока флаг был поднят, — и без этого отбора уезжали бы
    в разметку строками без статуса. Виджет на неизвестный статус подставляет зелёный, то есть
    «адрес решён»: цвет, означающий ровно противоположное тому, что произошло.
    """
    rows = (
        AddressObservationBucket.objects.filter(
            domain_id=domain_id, bucket_size=BucketSize.DAY.value
        )
        .values_list("address", "bucket_start")
        .order_by("bucket_start")
    )

    seen: dict[str, list[date]] = {}
    for address, bucket_start in rows:
        name = str(address)
        if name not in statuses:
            continue
        seen.setdefault(name, []).append(bucket_start.date())

    if not seen:
        return TraceData(start=at.date().isoformat(), days=1, rows=[])

    first = min(day for days in seen.values() for day in days)
    total = (at.date() - first).days + 1

    return TraceData(
        start=first.isoformat(),
        days=total,
        rows=[
            TraceRow(
                address=address,
                status=statuses[address],
                days=sorted({(day - first).days for day in days}),
            )
            for address, days in seen.items()
        ],
    )
