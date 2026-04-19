from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ymca.memo import (
    SENTINEL_PAYEE_NAME,
    amount_text_to_milliunits,
    append_fx_marker,
    build_fx_marker,
    build_fx_marker_from_amount_text,
    build_sentinel_memo,
    format_balance_milliunits,
    format_memo_milliunits,
    format_milliunits,
    has_fx_marker,
    has_legacy_fx_marker,
    is_sentinel_payee,
    memo_marker_currency,
    memo_marker_has_transfer_prefix,
    parse_sentinel_memo,
    replace_legacy_fx_marker,
    source_amount_milliunits_from_marker,
)


def test_build_and_detect_fx_marker() -> None:
    marker = build_fx_marker(
        source_amount_milliunits=12340,
        source_currency="HKD",
        rate_text="7.8",
        pair_label="HKD/USD",
    )

    assert marker == "[FX] 12.34 HKD (rate: 7.8 HKD/USD)"
    assert has_fx_marker(marker) is True


def test_append_fx_marker_preserves_existing_memo() -> None:
    marker = build_fx_marker(
        source_amount_milliunits=-12340,
        source_currency="HKD",
        rate_text="7.8",
        pair_label="HKD/USD",
    )

    memo = append_fx_marker("Dinner", marker)

    assert memo == "Dinner | [FX] -12.34 HKD (rate: 7.8 HKD/USD)"


def test_build_fx_marker_for_transfer_uses_literal_plus_minus_prefix() -> None:
    marker = build_fx_marker(
        source_amount_milliunits=12340,
        source_currency="HKD",
        rate_text="7.8",
        pair_label="HKD/USD",
        transfer_prefix=True,
    )

    assert marker == "[FX] +/-12.34 HKD (rate: 7.8 HKD/USD)"


def test_build_fx_marker_uses_thousands_delimiter() -> None:
    marker = build_fx_marker(
        source_amount_milliunits=-45586690,
        source_currency="HKD",
        rate_text="0.12821",
        pair_label="HKD/USD",
        transfer_prefix=True,
    )

    assert marker == "[FX] +/-45,586.69 HKD (rate: 0.12821 HKD/USD)"
    assert has_fx_marker(marker) is True


def test_build_fx_marker_trims_trailing_zero_after_decimal() -> None:
    marker = build_fx_marker(
        source_amount_milliunits=1234500,
        source_currency="HKD",
        rate_text="7.8",
        pair_label="HKD/USD",
    )

    assert marker == "[FX] 1,234.5 HKD (rate: 7.8 HKD/USD)"


def test_build_fx_marker_drops_decimal_for_round_amount() -> None:
    marker = build_fx_marker(
        source_amount_milliunits=1234000,
        source_currency="HKD",
        rate_text="7.8",
        pair_label="HKD/USD",
    )

    assert marker == "[FX] 1,234 HKD (rate: 7.8 HKD/USD)"


def test_replace_legacy_fx_marker_rewrites_old_format() -> None:
    original = "Dinner | 12.34 HKD (FX rate: 7.8)"

    rewritten = replace_legacy_fx_marker(
        original,
        pair_label_for_currency={"HKD": "USD/HKD"},
        transfer=False,
    )

    assert has_legacy_fx_marker(original) is True
    assert rewritten == "Dinner | [FX] 12.34 HKD (rate: 7.8 USD/HKD)"


def test_replace_legacy_fx_marker_rewrites_old_format_with_thousands_delimiter() -> None:
    original = "Transfer | -45,586.69 HKD (FX rate: 0.12821)"

    rewritten = replace_legacy_fx_marker(
        original,
        pair_label_for_currency={"HKD": "USD/HKD"},
        transfer=True,
    )

    assert has_legacy_fx_marker(original) is True
    assert rewritten == "Transfer | [FX] -45,586.69 HKD (rate: 0.12821 USD/HKD)"


def test_build_fx_marker_from_amount_text_preserves_legacy_sign_string() -> None:
    marker = build_fx_marker_from_amount_text(
        amount_text="-/+78",
        source_currency="HKD",
        rate_text="0.12821",
        pair_label="USD/HKD",
    )

    assert marker == "[FX] -/+78 HKD (rate: 0.12821 USD/HKD)"
    assert has_fx_marker(marker) is True


def test_build_fx_marker_from_amount_text_normalizes_thousands_delimiter() -> None:
    marker = build_fx_marker_from_amount_text(
        amount_text="-7500",
        source_currency="HKD",
        rate_text="0.12821",
        pair_label="USD/HKD",
    )

    assert marker == "[FX] -7,500 HKD (rate: 0.12821 USD/HKD)"


def test_replace_legacy_fx_marker_rewrites_round_amount_without_decimals() -> None:
    original = "Lunch | 78 HKD (FX rate: 0.12821)"

    rewritten = replace_legacy_fx_marker(
        original,
        pair_label_for_currency={"HKD": "USD/HKD"},
        transfer=False,
    )

    assert has_legacy_fx_marker(original) is True
    assert rewritten == "Lunch | [FX] 78 HKD (rate: 0.12821 USD/HKD)"


def test_replace_legacy_fx_marker_rewrites_amount_with_single_decimal() -> None:
    original = "-1,470.5 HKD (FX rate: 0.12821)"

    rewritten = replace_legacy_fx_marker(
        original,
        pair_label_for_currency={"HKD": "USD/HKD"},
        transfer=False,
    )

    assert has_legacy_fx_marker(original) is True
    assert rewritten == "[FX] -1,470.5 HKD (rate: 0.12821 USD/HKD)"


def test_replace_legacy_fx_marker_normalizes_ungrouped_thousands() -> None:
    original = "-7500 HKD (FX rate: 0.12821)"

    rewritten = replace_legacy_fx_marker(
        original,
        pair_label_for_currency={"HKD": "USD/HKD"},
        transfer=False,
    )

    assert has_legacy_fx_marker(original) is True
    assert rewritten == "[FX] -7,500 HKD (rate: 0.12821 USD/HKD)"


def test_replace_legacy_fx_marker_moves_prepended_marker_to_end() -> None:
    original = "-/+78 HKD (FX rate: 0.12821) · FPS"

    rewritten = replace_legacy_fx_marker(
        original,
        pair_label_for_currency={"HKD": "USD/HKD"},
        transfer=True,
    )

    assert has_legacy_fx_marker(original) is True
    assert rewritten == "FPS | [FX] -/+78 HKD (rate: 0.12821 USD/HKD)"


def test_has_fx_marker_returns_false_for_none_and_plain_memo() -> None:
    assert has_fx_marker(None) is False
    assert has_fx_marker("plain memo") is False


def test_has_legacy_fx_marker_returns_false_for_none_and_plain_memo() -> None:
    assert has_legacy_fx_marker(None) is False
    assert has_legacy_fx_marker("plain memo") is False


def test_replace_legacy_fx_marker_returns_none_when_no_legacy_match() -> None:
    assert (
        replace_legacy_fx_marker(
            "Dinner", pair_label_for_currency={"HKD": "USD/HKD"}, transfer=False
        )
        is None
    )


def test_replace_legacy_fx_marker_returns_none_when_currency_unconfigured() -> None:
    original = "Dinner | 10.00 HKD (FX rate: 0.1282)"

    assert (
        replace_legacy_fx_marker(
            original,
            pair_label_for_currency={},
            transfer=False,
        )
        is None
    )


def test_amount_text_to_milliunits_rejects_unsupported_text() -> None:
    with pytest.raises(ValueError, match="Unsupported amount text"):
        amount_text_to_milliunits("not-a-number")


def test_amount_text_to_milliunits_uses_fallback_sign_for_transfer_pair() -> None:
    assert amount_text_to_milliunits("+/-1", fallback_sign=-1000) == -1000
    assert amount_text_to_milliunits("+/-1", fallback_sign=1000) == 1000


def test_format_milliunits_rejects_invalid_places() -> None:
    with pytest.raises(ValueError, match="places must be 2 or 3"):
        format_milliunits(12340, places=4)


def test_format_milliunits_accepts_two_and_three_decimal_places() -> None:
    assert format_milliunits(12345, places=2) == "12.35"
    assert format_milliunits(12345, places=3) == "12.345"


def test_format_milliunits_always_show_sign_emits_plus() -> None:
    assert format_milliunits(12345, places=2, always_show_sign=True) == "+12.35"


def test_format_milliunits_formats_zero_without_negative_sign() -> None:
    assert format_milliunits(0, places=2) == "0.00"
    assert format_milliunits(-0, places=2) == "0.00"


def test_format_memo_milliunits_drops_decimals_for_round_values() -> None:
    assert format_memo_milliunits(12000) == "12"
    assert format_memo_milliunits(-12340) == "-12.34"


def test_format_memo_milliunits_transfer_prefix_uses_absolute_value() -> None:
    assert format_memo_milliunits(-12340, transfer_prefix=True) == "+/-12.34"
    assert format_memo_milliunits(0, transfer_prefix=True) == "+/-0"


def test_build_fx_marker_from_amount_text_returns_input_when_unparseable() -> None:
    marker = build_fx_marker_from_amount_text(
        amount_text="nope",
        source_currency="HKD",
        rate_text="0.12821",
        pair_label="USD/HKD",
    )

    assert marker == "[FX] nope HKD (rate: 0.12821 USD/HKD)"


def test_amount_text_to_milliunits_uses_sign_prefix_when_no_fallback_sign() -> None:
    assert amount_text_to_milliunits("+/-1") == 1000
    assert amount_text_to_milliunits("-/+1") == -1000
    assert amount_text_to_milliunits("+/-1", fallback_sign=0) == 1000
    assert amount_text_to_milliunits("-/+1", fallback_sign=0) == -1000


def test_format_balance_milliunits_keeps_two_decimals_and_thousands() -> None:
    assert format_balance_milliunits(1234560) == "1,234.56"
    assert format_balance_milliunits(-500000) == "-500.00"
    assert format_balance_milliunits(0) == "0.00"
    assert format_balance_milliunits(-1) == "0.00"


def test_build_sentinel_memo_first_time_omits_prev_section() -> None:
    memo = build_sentinel_memo(
        currency="HKD",
        balance_milliunits=1234560,
        rate_text="7.8",
        pair_label="HKD/USD",
        updated_at=datetime(2026, 4, 19, 14, 30, 45, tzinfo=UTC),
        drift_milliunits_stronger=0,
        stronger_currency="USD",
    )

    assert memo == (
        "[YMCA-BAL] HKD 1,234.56 | rate 7.8 HKD/USD | "
        "updated 2026-04-19T14:30:45Z | drift 0.00 USD"
    )


def test_build_sentinel_memo_includes_prev_section_when_provided() -> None:
    memo = build_sentinel_memo(
        currency="HKD",
        balance_milliunits=1234560,
        rate_text="7.8",
        pair_label="HKD/USD",
        updated_at=datetime(2026, 4, 19, 14, 30, 45, tzinfo=UTC),
        prev_balance_milliunits=1200000,
        prev_updated_at=datetime(2026, 4, 18, 14, 30, 45, tzinfo=UTC),
        drift_milliunits_stronger=-50,
        stronger_currency="USD",
    )

    assert memo == (
        "[YMCA-BAL] HKD 1,234.56 | rate 7.8 HKD/USD | "
        "updated 2026-04-19T14:30:45Z | "
        "prev 1,200.00 2026-04-18T14:30:45Z | drift -0.05 USD"
    )


def test_parse_sentinel_memo_round_trip_with_prev() -> None:
    memo = build_sentinel_memo(
        currency="GBP",
        balance_milliunits=-120050,
        rate_text="1.35",
        pair_label="USD/GBP",
        updated_at=datetime(2026, 4, 19, 14, 30, 45, tzinfo=UTC),
        prev_balance_milliunits=-100000,
        prev_updated_at=datetime(2026, 4, 18, 14, 30, 45, tzinfo=UTC),
        drift_milliunits_stronger=20,
        stronger_currency="GBP",
    )

    parsed = parse_sentinel_memo(memo)

    assert parsed is not None
    assert parsed["currency"] == "GBP"
    assert parsed["balance_milliunits"] == -120050
    assert parsed["rate_text"] == "1.35"
    assert parsed["pair_label"] == "USD/GBP"
    assert parsed["prev_balance_milliunits"] == -100000
    assert parsed["drift_milliunits_stronger"] == 20
    assert parsed["stronger_currency"] == "GBP"


def test_parse_sentinel_memo_returns_none_for_nonsentinel_text() -> None:
    assert parse_sentinel_memo(None) is None
    assert parse_sentinel_memo("plain memo") is None
    assert parse_sentinel_memo("[FX] 12.34 HKD (rate: 7.8 HKD/USD)") is None


def test_is_sentinel_payee_exact_match_only() -> None:
    assert is_sentinel_payee(SENTINEL_PAYEE_NAME) is True
    assert is_sentinel_payee(None) is False
    assert is_sentinel_payee("ymca tracked balance") is False
    assert is_sentinel_payee(f"{SENTINEL_PAYEE_NAME} (v2)") is False


def test_source_amount_milliunits_from_marker_reads_current_marker() -> None:
    memo = "Dinner | [FX] -12.34 HKD (rate: 7.8 HKD/USD)"
    assert source_amount_milliunits_from_marker(memo) == -12340


def test_source_amount_milliunits_from_marker_reads_legacy_marker() -> None:
    memo = "Lunch | 78 HKD (FX rate: 0.12821)"
    assert source_amount_milliunits_from_marker(memo) == 78000


def test_source_amount_milliunits_from_marker_uses_fallback_sign_for_plus_minus() -> None:
    memo = "Move | [FX] +/-12.34 HKD (rate: 7.8 HKD/USD)"
    assert source_amount_milliunits_from_marker(memo, fallback_sign=-1) == -12340
    assert source_amount_milliunits_from_marker(memo, fallback_sign=1) == 12340


def test_source_amount_milliunits_from_marker_returns_none_without_marker() -> None:
    assert source_amount_milliunits_from_marker(None) is None
    assert source_amount_milliunits_from_marker("Just a plain memo") is None


def test_memo_marker_has_transfer_prefix_detects_literal_symbols() -> None:
    assert memo_marker_has_transfer_prefix("[FX] +/-12.34 HKD (rate: 7.8 HKD/USD)") is True
    assert memo_marker_has_transfer_prefix("[FX] 12.34 HKD (rate: 7.8 HKD/USD)") is False
    assert memo_marker_has_transfer_prefix("78 HKD (FX rate: 0.12821)") is False
    assert memo_marker_has_transfer_prefix(None) is False
    assert memo_marker_has_transfer_prefix("plain memo") is False


def test_memo_marker_currency_returns_source_currency_for_both_formats() -> None:
    assert memo_marker_currency("[FX] 12.34 HKD (rate: 7.8 HKD/USD)") == "HKD"
    assert memo_marker_currency("78 GBP (FX rate: 1.35)") == "GBP"
    assert memo_marker_currency(None) is None
    assert memo_marker_currency("plain") is None
