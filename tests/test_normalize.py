"""Pins the verified data traps from research.md §2 as regression tests."""

import pytest

from bball.models import PlayerSeasonRecord, Source
from bball.normalize import (
    canonical_name,
    display_name,
    parse_minutes,
    parse_optional_float,
    parse_optional_int,
)


# --- canonical_name: the three hard cases from research.md ----------------

@pytest.mark.parametrize(
    "realgm_raw, api_form",
    [
        ("Agravánis, Dimítrios", "Dimitrios Agravanis"),
        ("Steinarsson, Ægir", "Aegir Steinarsson"),
        ("Cerro, Adrian del", "Adrian del Cerro"),
    ],
)
def test_canonical_name_matches_api_form(realgm_raw, api_form):
    assert canonical_name(realgm_raw) == canonical_name(api_form)


def test_canonical_name_folds_aegir_explicitly():
    # Regression guard: NFKD alone does NOT decompose Æ/æ, only the diacritic
    # marks (á, í, é, ...). If someone "simplifies" canonical_name to rely on
    # NFKD alone, this must fail.
    assert canonical_name("Ægir") == "aegir"
    assert canonical_name("Steinarsson, Ægir") == "aegir steinarsson"


def test_canonical_name_multi_token_surname():
    assert canonical_name("Cerro, Adrian del") == "adrian del cerro"


def test_canonical_name_casefold_and_whitespace():
    assert canonical_name("  Dimitrios   Agravanis ") == "dimitrios agravanis"
    assert canonical_name("DIMITRIOS AGRAVANIS") == canonical_name("dimitrios agravanis")


def test_canonical_name_no_comma_is_unchanged_order():
    assert canonical_name("Dimitrios Agravanis") == "dimitrios agravanis"


# --- display_name: flips but preserves diacritics --------------------------

def test_display_name_flips_comma_without_folding():
    assert display_name("Cerro, Adrian del") == "Adrian del Cerro"
    assert display_name("Steinarsson, Ægir") == "Ægir Steinarsson"


def test_display_name_no_comma_passthrough():
    assert display_name("Dimitrios Agravanis") == "Dimitrios Agravanis"


# --- parse_minutes: decimal and MM:SS, verified rows ------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("20.5", 20.5),
        ("26:36", 26.6),  # Agravanis 2020-21
        ("25:06", 25.1),  # Agravanis 2021-22
        ("17:42", 17.7),  # Agravanis 2022-23
        ("9:42", 9.7),    # Agravanis 2025-26
        ("", None),
        (None, None),
        ("   ", None),
        (0, 0.0),
    ],
)
def test_parse_minutes_valid(raw, expected):
    assert parse_minutes(raw) == expected


@pytest.mark.parametrize("raw", ["abc", "12:99", "-5", "1:2:3", ":30"])
def test_parse_minutes_invalid_raises(raw):
    with pytest.raises(ValueError):
        parse_minutes(raw)


# --- optional coercions: empty cells -> None, garbage -> raise -------------

@pytest.mark.parametrize("raw", [None, "", "  "])
def test_parse_optional_float_empty_is_none(raw):
    assert parse_optional_float(raw) is None


def test_parse_optional_float_parses():
    assert parse_optional_float("36.9") == 36.9


def test_parse_optional_float_garbage_raises():
    with pytest.raises(ValueError):
        parse_optional_float("n/a")


@pytest.mark.parametrize("raw", [None, "", "  "])
def test_parse_optional_int_empty_is_none(raw):
    assert parse_optional_int(raw) is None


def test_parse_optional_int_parses():
    assert parse_optional_int("26") == 26


def test_parse_optional_int_garbage_raises():
    with pytest.raises(ValueError):
        parse_optional_int("n/a")


# --- PlayerSeasonRecord smoke test ------------------------------------------

def test_player_season_record_valid_construction():
    rec = PlayerSeasonRecord(
        source=Source.WISELY_API,
        source_key="26515",
        full_name="Dimitrios Agravanis",
        canonical_name=canonical_name("Dimitrios Agravanis"),
        season="2020-21",
        team="ASP Promitheas Patras",
        league="HEBA A1",
        age=26,
        gp=31,
        min_pg=26.6,
        pts_pg=13.9,
        reb_pg=5.9,
        usage_pct=24.8,
        ts_pct_api=0.569,
        reb_pct=13.6,
        per=19.6,
        bpm=12.09,
    )
    assert rec.season == "2020-21"
    assert rec.canonical_name == "dimitrios agravanis"


@pytest.mark.parametrize("bad_season", ["2020", "20-21", "2020/21", ""])
def test_player_season_record_rejects_bad_season(bad_season):
    with pytest.raises(ValueError):
        PlayerSeasonRecord(
            source=Source.REALGM,
            source_key="x",
            full_name="X",
            canonical_name="x",
            season=bad_season,
        )
