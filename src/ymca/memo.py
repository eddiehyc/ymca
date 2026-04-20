from __future__ import annotations

import re
from datetime import datetime
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
    r"\[FX(?P<counted>\+)?\]\s+"
    rf"(?P<amount>{_AMOUNT_PATTERN})\s+"
    r"(?P<currency>[A-Z]{3})\s+"
    r"\(rate:\s*(?P<rate>[0-9]+(?:\.[0-9]+)?)\s+(?P<pair>[A-Z]{3}/[A-Z]{3})\)"
)
"""Matches either the ``[FX]`` (uncounted) or ``[FX+]`` (counted) current-form
FX marker. The ``counted`` capture group is ``"+"`` for ``[FX+]`` and ``None``
for ``[FX]``; see §12 of ``docs/spec.md`` for the semantics.
"""
LEGACY_FX_MARKER_RE = re.compile(
    rf"(?P<amount>{_AMOUNT_PATTERN})\s+"
    r"(?P<currency>[A-Z]{3})\s+"
    r"\(FX rate:\s*(?P<rate>[0-9]+(?:\.[0-9]+)?)\)"
)

SENTINEL_PAYEE_NAME = "[YMCA] Tracked Balance"
SENTINEL_FLAG_COLOR = "green"
"""YNAB flag color applied to every sentinel transaction.

Green was chosen for two reasons: (1) the flag makes the sentinel row visually
distinctive in the YNAB register so users can spot it at a glance, and
(2) applying the same color on every sync makes the flag stable as a recovery
hint if a user accidentally clears it by hand.
"""

_SENTINEL_ISO_PATTERN = r"[0-9T:\-Z.+]+"
_SENTINEL_PATTERN = (
    r"^\[YMCA-BAL\]\s+"
    r"(?P<currency>[A-Z]{3})\s+"
    r"(?P<amount>" + _AMOUNT_PATTERN + r")\s+\|\s+"
    r"rate\s+(?P<rate>[0-9]+(?:\.[0-9]+)?)\s+(?P<pair>[A-Z]{3}/[A-Z]{3})\s+\|\s+"
    r"updated\s+(?P<updated>" + _SENTINEL_ISO_PATTERN + r")"
    r"(?:\s+\|\s+prev\s+(?P<prev_amount>" + _AMOUNT_PATTERN + r")"
    r"\s+(?P<prev_updated>" + _SENTINEL_ISO_PATTERN + r"))?"
    r"\s+\|\s+drift\s+(?P<drift>" + _AMOUNT_PATTERN + r")\s+(?P<stronger>[A-Z]{3})$"
)
SENTINEL_MEMO_RE = re.compile(_SENTINEL_PATTERN)


def has_fx_marker(memo: str | None) -> bool:
    if memo is None:
        return False
    return FX_MARKER_RE.search(memo) is not None


def has_fx_counted_marker(memo: str | None) -> bool:
    """Return True when ``memo`` contains the ``[FX+]`` (counted) bracket form.

    Returns False for ``[FX]``, for legacy ``(FX rate: ...)`` markers, and for
    any memo without an FX marker at all.
    """
    if memo is None:
        return False
    match = FX_MARKER_RE.search(memo)
    return match is not None and match.group("counted") == "+"


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
    counted: bool = False,
) -> str:
    """Build the current-form FX marker.

    ``counted=True`` emits the ``[FX+]`` variant, which means "this transaction
    has been added to the tracked local-currency balance". ``counted=False``
    (the default) emits the plain ``[FX]`` variant for converted-but-not-
    counted rows (uncleared transactions, or accounts that don't have
    ``track_local_balance`` enabled).
    """
    source_amount = format_memo_milliunits(
        source_amount_milliunits,
        transfer_prefix=transfer_prefix,
    )
    bracket = "[FX+]" if counted else "[FX]"
    return f"{bracket} {source_amount} {source_currency} (rate: {rate_text} {pair_label})"


def flip_fx_marker_counted(memo: str, *, counted: bool) -> str | None:
    """Flip the ``[FX]`` / ``[FX+]`` bracket in ``memo`` without touching the
    amount, currency, rate, or pair label.

    Returns the rewritten memo when the marker is already in current form and
    needed a flip. Returns the original memo unchanged when the bracket is
    already in the requested state. Returns ``None`` when ``memo`` does not
    contain a current-form marker (caller can then try legacy migration).
    """
    match = FX_MARKER_RE.search(memo)
    if match is None:
        return None
    current_bracket = "[FX+]" if match.group("counted") == "+" else "[FX]"
    new_bracket = "[FX+]" if counted else "[FX]"
    if current_bracket == new_bracket:
        return memo
    start = match.start()
    return memo[:start] + new_bracket + memo[start + len(current_bracket) :]


def append_fx_marker(original_memo: str | None, marker: str) -> str:
    if original_memo is None or not original_memo.strip():
        return marker
    return f"{original_memo.strip()} | {marker}"


def replace_legacy_fx_marker(
    memo: str,
    *,
    pair_label_for_currency: dict[str, str],
    transfer: bool,
    counted: bool = False,
) -> str | None:
    """Rewrite a legacy ``(FX rate: ...)`` marker into the current form.

    ``counted=True`` emits ``[FX+]`` (the row is currently part of the tracked
    balance); ``counted=False`` (default) preserves the historic behaviour of
    emitting ``[FX]``. The ``transfer`` argument is kept for API stability but
    is no longer consulted (the amount text already encodes any ``+/-`` sign).
    """
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
        counted=counted,
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
    counted: bool = False,
) -> str:
    normalized_amount = _normalize_amount_text(amount_text)
    bracket = "[FX+]" if counted else "[FX]"
    return f"{bracket} {normalized_amount} {source_currency} (rate: {rate_text} {pair_label})"


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


def format_balance_milliunits(amount_milliunits: int) -> str:
    """Format a signed balance for inclusion in a sentinel memo.

    Keeps two decimal places with thousands separators; does not drop trailing
    zeros (consistent fixed precision makes sentinel memos easy to scan).
    """
    amount = (Decimal(amount_milliunits) / _THOUSAND).quantize(
        _TWO_PLACES, rounding=ROUND_HALF_UP
    )
    if amount == 0:
        amount = abs(amount)
    return f"{amount:,.2f}"


def build_sentinel_memo(
    *,
    currency: str,
    balance_milliunits: int,
    rate_text: str,
    pair_label: str,
    updated_at: datetime,
    prev_balance_milliunits: int | None = None,
    prev_updated_at: datetime | None = None,
    drift_milliunits_stronger: int,
    stronger_currency: str,
) -> str:
    """Build the single-line sentinel memo string per spec §12.3."""
    amount_text = format_balance_milliunits(balance_milliunits)
    updated_text = _format_sentinel_datetime(updated_at)
    drift_text = format_balance_milliunits(drift_milliunits_stronger)

    parts = [
        f"[YMCA-BAL] {currency} {amount_text}",
        f"rate {rate_text} {pair_label}",
        f"updated {updated_text}",
    ]
    if prev_balance_milliunits is not None and prev_updated_at is not None:
        prev_amount_text = format_balance_milliunits(prev_balance_milliunits)
        prev_updated_text = _format_sentinel_datetime(prev_updated_at)
        parts.append(f"prev {prev_amount_text} {prev_updated_text}")
    parts.append(f"drift {drift_text} {stronger_currency}")
    return " | ".join(parts)


def parse_sentinel_memo(memo: str | None) -> dict[str, object] | None:
    """Parse a sentinel memo back into a dict of fields.

    Returns ``None`` when ``memo`` does not match the sentinel shape.
    """
    if memo is None:
        return None
    match = SENTINEL_MEMO_RE.match(memo.strip())
    if match is None:
        return None

    currency = match.group("currency")
    amount_milliunits = amount_text_to_milliunits(match.group("amount"))
    rate = match.group("rate")
    pair = match.group("pair")
    updated_at = _parse_sentinel_datetime(match.group("updated"))
    stronger = match.group("stronger")
    drift_milliunits = amount_text_to_milliunits(match.group("drift"))

    prev_amount_text = match.group("prev_amount")
    prev_updated_text = match.group("prev_updated")
    prev_balance_milliunits: int | None = None
    prev_updated_at: datetime | None = None
    if prev_amount_text is not None and prev_updated_text is not None:
        prev_balance_milliunits = amount_text_to_milliunits(prev_amount_text)
        prev_updated_at = _parse_sentinel_datetime(prev_updated_text)

    return {
        "currency": currency,
        "balance_milliunits": amount_milliunits,
        "rate_text": rate,
        "pair_label": pair,
        "updated_at": updated_at,
        "prev_balance_milliunits": prev_balance_milliunits,
        "prev_updated_at": prev_updated_at,
        "drift_milliunits_stronger": drift_milliunits,
        "stronger_currency": stronger,
    }


def is_sentinel_payee(payee_name: str | None) -> bool:
    return payee_name == SENTINEL_PAYEE_NAME


def source_amount_milliunits_from_marker(
    memo: str | None,
    *,
    fallback_sign: int | None = None,
) -> int | None:
    """Return the signed source-currency amount embedded in an FX marker.

    Tries the current ``[FX]`` marker first, then the legacy ``(FX rate: ...)``
    form. Returns ``None`` when no marker is present. When the memo encodes
    a transfer (``+/-`` literal), ``fallback_sign`` is used to resolve the
    direction; pass ``None`` if the caller needs the caller-side prompt.
    """
    if memo is None:
        return None
    for pattern in (FX_MARKER_RE, LEGACY_FX_MARKER_RE):
        match = pattern.search(memo)
        if match is None:
            continue
        amount_text = match.group("amount")
        return amount_text_to_milliunits(amount_text, fallback_sign=fallback_sign)
    return None


def memo_marker_has_transfer_prefix(memo: str | None) -> bool:
    """Return True when the FX marker in ``memo`` carries the ``+/-`` literal."""
    if memo is None:
        return False
    for pattern in (FX_MARKER_RE, LEGACY_FX_MARKER_RE):
        match = pattern.search(memo)
        if match is None:
            continue
        amount_text = match.group("amount")
        return amount_text.startswith("+/-") or amount_text.startswith("-/+")
    return False


def memo_marker_currency(memo: str | None) -> str | None:
    """Return the source currency from an FX marker, or None if absent."""
    if memo is None:
        return None
    for pattern in (FX_MARKER_RE, LEGACY_FX_MARKER_RE):
        match = pattern.search(memo)
        if match is not None:
            return match.group("currency")
    return None


def _format_sentinel_datetime(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_sentinel_datetime(text: str) -> datetime:
    normalized = text.rstrip("Z")
    return datetime.fromisoformat(normalized)
