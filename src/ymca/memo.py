from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

_THOUSAND = Decimal("1000")
_TWO_PLACES = Decimal("0.01")
_THREE_PLACES = Decimal("0.001")
_WHOLE_MILLIUNIT = Decimal("1")
_AMOUNT_SIGN_PATTERN = r"(?:[+-]/[+-]|[+-])?"
_AMOUNT_NUMBER_PATTERN = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_AMOUNT_PATTERN = rf"{_AMOUNT_SIGN_PATTERN}{_AMOUNT_NUMBER_PATTERN}"
_SEPARATOR_PATTERN = r"(?:\||·)"
_NORMALIZE_AMOUNT_RE = re.compile(
    rf"(?P<sign>{_AMOUNT_SIGN_PATTERN})(?P<number>{_AMOUNT_NUMBER_PATTERN})"
)

FX_MARKER_RE = re.compile(
    r"\[FX\]\s+"
    rf"(?P<amount>{_AMOUNT_PATTERN})\s+"
    r"(?P<currency>[A-Z]{3})\s+"
    r"\(rate:\s*(?P<rate>[0-9]+(?:\.[0-9]+)?)\s+(?P<pair>[A-Z]{3}/[A-Z]{3})\)"
)
LEGACY_FX_MARKER_RE = re.compile(
    rf"(?P<amount>{_AMOUNT_PATTERN})\s+"
    r"(?P<currency>[A-Z]{3})\s+"
    r"\(FX rate:\s*(?P<rate>[0-9]+(?:\.[0-9]+)?)\)"
)


def has_fx_marker(memo: str | None) -> bool:
    if memo is None:
        return False
    return FX_MARKER_RE.search(memo) is not None


def has_legacy_fx_marker(memo: str | None) -> bool:
    if memo is None:
        return False
    return LEGACY_FX_MARKER_RE.search(memo) is not None


def build_fx_marker(
    *,
    source_amount_milliunits: int,
    source_currency: str,
    rate_text: str,
    pair_label: str,
    transfer_prefix: bool = False,
) -> str:
    source_amount = format_memo_milliunits(
        source_amount_milliunits,
        transfer_prefix=transfer_prefix,
    )
    return f"[FX] {source_amount} {source_currency} (rate: {rate_text} {pair_label})"


def append_fx_marker(original_memo: str | None, marker: str) -> str:
    if original_memo is None or not original_memo.strip():
        return marker
    return f"{original_memo.strip()} | {marker}"


def replace_legacy_fx_marker(
    memo: str,
    *,
    pair_label_for_currency: dict[str, str],
    transfer: bool,
) -> str | None:
    del transfer

    match = LEGACY_FX_MARKER_RE.search(memo)
    if match is None:
        return None

    currency = match.group("currency")
    pair_label = pair_label_for_currency.get(currency)
    if pair_label is None:
        return None

    marker = build_fx_marker_from_amount_text(
        amount_text=match.group("amount"),
        source_currency=currency,
        rate_text=match.group("rate"),
        pair_label=pair_label,
    )
    before = _trim_legacy_separator_suffix(memo[: match.start()])
    after = _trim_legacy_separator_prefix(memo[match.end() :])

    parts = [part for part in (before, after, marker) if part]
    rewritten = " | ".join(parts)
    if rewritten == memo:
        return None
    return rewritten


def build_fx_marker_from_amount_text(
    *,
    amount_text: str,
    source_currency: str,
    rate_text: str,
    pair_label: str,
) -> str:
    normalized_amount = _normalize_amount_text(amount_text)
    return f"[FX] {normalized_amount} {source_currency} (rate: {rate_text} {pair_label})"


def amount_text_to_milliunits(
    amount_text: str,
    *,
    fallback_sign: int | None = None,
) -> int:
    match = _NORMALIZE_AMOUNT_RE.fullmatch(amount_text)
    if match is None:
        raise ValueError(f"Unsupported amount text: {amount_text!r}")

    sign_token = match.group("sign") or ""
    number = match.group("number")
    sign = _resolve_amount_sign(sign_token, fallback_sign=fallback_sign)
    magnitude = (Decimal(number.replace(",", "")) * _THOUSAND).quantize(
        _WHOLE_MILLIUNIT,
        rounding=ROUND_HALF_UP,
    )
    return sign * int(magnitude)


def format_milliunits(
    amount_milliunits: int,
    *,
    places: int,
    always_show_sign: bool = False,
) -> str:
    if places not in {2, 3}:
        raise ValueError("places must be 2 or 3")

    quantum = _TWO_PLACES if places == 2 else _THREE_PLACES
    amount = (Decimal(amount_milliunits) / _THOUSAND).quantize(quantum, rounding=ROUND_HALF_UP)
    if amount == 0:
        amount = abs(amount)
    sign = "+" if always_show_sign else ""
    return f"{amount:{sign},.{places}f}"


def format_memo_milliunits(
    amount_milliunits: int,
    *,
    transfer_prefix: bool = False,
) -> str:
    amount_value = amount_milliunits
    if transfer_prefix:
        amount_value = abs(amount_value)

    amount = (Decimal(amount_value) / _THOUSAND).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
    if amount == 0:
        amount = abs(amount)

    text = f"{amount:,.2f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if transfer_prefix:
        return f"+/-{text}"
    return text


def _milliunits_from_amount_text(amount_text: str) -> int:
    amount = (Decimal(amount_text.replace(",", "")) * _THOUSAND).quantize(
        _WHOLE_MILLIUNIT,
        rounding=ROUND_HALF_UP,
    )
    return int(amount)


def _trim_legacy_separator_suffix(text: str) -> str:
    return re.sub(rf"(?:\s*{_SEPARATOR_PATTERN}\s*)+$", "", text).strip()


def _trim_legacy_separator_prefix(text: str) -> str:
    return re.sub(rf"^(?:\s*{_SEPARATOR_PATTERN}\s*)+", "", text).strip()


def _normalize_amount_text(amount_text: str) -> str:
    match = _NORMALIZE_AMOUNT_RE.fullmatch(amount_text)
    if match is None:
        return amount_text

    sign = match.group("sign") or ""
    number = match.group("number")
    integer_part, dot, fractional_part = number.partition(".")
    grouped_integer = f"{int(integer_part.replace(',', '')):,}"
    if not dot:
        return f"{sign}{grouped_integer}"
    return f"{sign}{grouped_integer}.{fractional_part}"


def _resolve_amount_sign(sign_token: str, *, fallback_sign: int | None) -> int:
    if sign_token == "-":
        return -1
    if sign_token in {"", "+"}:
        return 1
    if fallback_sign is not None and fallback_sign != 0:
        return -1 if fallback_sign < 0 else 1
    return -1 if sign_token.startswith("-") else 1
