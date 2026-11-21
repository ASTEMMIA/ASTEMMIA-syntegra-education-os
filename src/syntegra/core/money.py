"""Money as BIGINT minor units plus an ISO 4217 currency (CEDA §6.1)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

# Minor-unit exponents for currencies in launch scope; default 2.
_EXPONENT: dict[str, int] = {"NGN": 2, "GBP": 2, "USD": 2, "EUR": 2, "JPY": 0, "KWD": 3}


class CurrencyMismatch(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Money:
    minor: int
    currency: str

    def __post_init__(self) -> None:
        if len(self.currency) != 3 or not self.currency.isalpha() or not self.currency.isupper():
            raise ValueError("currency must be an ISO 4217 alphabetic code")
        if not isinstance(self.minor, int):
            raise TypeError("minor units must be an int")

    @staticmethod
    def exponent(currency: str) -> int:
        return _EXPONENT.get(currency, 2)

    @classmethod
    def of(cls, amount: str | Decimal, currency: str) -> Money:
        exp = cls.exponent(currency)
        q = Decimal(amount).scaleb(exp).quantize(Decimal(1), rounding=ROUND_HALF_EVEN)
        return cls(int(q), currency)

    @classmethod
    def zero(cls, currency: str) -> Money:
        return cls(0, currency)

    def _check(self, other: Money) -> None:
        if other.currency != self.currency:
            raise CurrencyMismatch(f"{self.currency} vs {other.currency}")

    def __add__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.minor + other.minor, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.minor - other.minor, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.minor, self.currency)

    def allocate(self, ratios: list[int]) -> list[Money]:
        """Split without losing a minor unit (largest-remainder on the first parts)."""
        if not ratios or any(r < 0 for r in ratios) or sum(ratios) == 0:
            raise ValueError("ratios must be non-negative and not all zero")
        total = sum(ratios)
        parts = [self.minor * r // total for r in ratios]
        remainder = self.minor - sum(parts)
        for i in range(abs(remainder)):
            parts[i % len(parts)] += 1 if remainder > 0 else -1
        return [Money(p, self.currency) for p in parts]

    def as_decimal(self) -> Decimal:
        return Decimal(self.minor).scaleb(-self.exponent(self.currency))

    def __str__(self) -> str:
        return f"{self.as_decimal():.{self.exponent(self.currency)}f} {self.currency}"
