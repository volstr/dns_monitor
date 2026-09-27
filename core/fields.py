"""
Поля под специализированные типы PostgreSQL ``inet`` и ``cidr`` — ТЗ 4.0.

``django-netfields`` не подключается: запрос вхождения в продукте один, и ради него не нужна
зависимость (plan.md, этап 2).

Разделение типов намеренное и следует из Q-13.3:

- ``CidrField`` — запись белого списка. Тип ``cidr`` сам отвергает подсеть с ненулевой хостовой
  частью, поэтому инвариант держится типом, а не дисциплиной вызывающего кода.
- ``InetField`` — отдельный адрес в реестре и в корзинах наблюдений.

Вхождение адреса в подсеть выполняется одним запросом через ``__net_contains_or_equals``
(оператор ``>>=``) с индексом GiST ``inet_ops``.
"""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING, Any

from django.core.exceptions import ValidationError
from django.db import models

from core.types.dns import IPAddress, IPNetwork

# Django не поддерживает подписку своих полей в рантайме, а mypy --strict требует параметров типа.
if TYPE_CHECKING:
    _CidrBase = models.Field[IPNetwork | str | None, IPNetwork | None]
    _InetBase = models.Field[IPAddress | str | None, IPAddress | None]
    _LookupBase = models.Lookup[Any]
else:
    _CidrBase = models.Field
    _InetBase = models.Field
    _LookupBase = models.Lookup


class CidrField(_CidrBase):
    """Подсеть или отдельный адрес с полной маской. Колонка ``cidr``."""

    description = "Подсеть в нотации CIDR (ТЗ 10.2)"
    empty_strings_allowed = False

    def db_type(self, connection: Any) -> str:
        return "cidr"

    def from_db_value(
        self, value: Any, expression: Any = None, connection: Any = None
    ) -> IPNetwork | None:
        return self.to_python(value)

    def to_python(self, value: Any) -> IPNetwork | None:
        if value is None or isinstance(value, ipaddress.IPv4Network | ipaddress.IPv6Network):
            return value
        try:
            # strict=True: 10.20.1.5/24 отвергается, как и самим типом cidr.
            return ipaddress.ip_network(str(value), strict=True)
        except ValueError as error:
            raise ValidationError(str(error), code="invalid") from error

    def get_prep_value(self, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, ipaddress.IPv4Address | ipaddress.IPv6Address):
            # Адрес в запросе вхождения — это подсеть с полной маской (ТЗ 6.3).
            return str(ipaddress.ip_network(value))
        network = self.to_python(value)
        return None if network is None else str(network)


class InetField(_InetBase):
    """Отдельный IP-адрес. Колонка ``inet``."""

    description = "IP-адрес (ТЗ 4.0)"
    empty_strings_allowed = False

    def db_type(self, connection: Any) -> str:
        return "inet"

    def from_db_value(
        self, value: Any, expression: Any = None, connection: Any = None
    ) -> IPAddress | None:
        return self.to_python(value)

    def to_python(self, value: Any) -> IPAddress | None:
        if value is None or isinstance(value, ipaddress.IPv4Address | ipaddress.IPv6Address):
            return value
        if isinstance(value, ipaddress.IPv4Interface | ipaddress.IPv6Interface):
            return value.ip
        try:
            return ipaddress.ip_address(str(value).split("/")[0])
        except ValueError as error:
            raise ValidationError(str(error), code="invalid") from error

    def get_prep_value(self, value: Any) -> str | None:
        address = self.to_python(value)
        return None if address is None else str(address)


@CidrField.register_lookup
class NetContainsOrEquals(_LookupBase):
    """
    ``entry >>= адрес`` — вхождение адреса в подсеть одним запросом (ТЗ 6.3).

    Разные семейства адресов PostgreSQL сравнивает как ``false``, а не как ошибку, поэтому
    требование 6.3 «в пределах одного семейства» выполняется самим оператором.
    """

    lookup_name = "net_contains_or_equals"

    def as_sql(self, compiler: Any, connection: Any) -> tuple[str, list[Any]]:
        lhs, lhs_params = self.process_lhs(compiler, connection)
        rhs, rhs_params = self.process_rhs(compiler, connection)
        return f"{lhs} >>= {rhs}", list(lhs_params) + list(rhs_params)
