from __future__ import annotations

from ymca.memo import (
    append_fx_marker,
    build_fx_marker,
    build_fx_marker_from_amount_text,
    has_fx_marker,
    has_legacy_fx_marker,
    replace_legacy_fx_marker,
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
