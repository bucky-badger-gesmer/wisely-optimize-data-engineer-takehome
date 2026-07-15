"""Pins the verified per-source counts and traps from research.md §2."""

from pathlib import Path

import pytest

from bball.adapters.realgm import RealGMAdapter
from bball.adapters.wisely_api import WiselyApiAdapter
from bball.models import PlayerSeasonRecord, Rejection

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _split(items):
    items = list(items)  # materialize once: items may be a one-shot generator
    records = [i for i in items if isinstance(i, PlayerSeasonRecord)]
    rejections = [i for i in items if isinstance(i, Rejection)]
    return records, rejections


# --- Wisely API adapter -----------------------------------------------------

def test_api_adapter_yields_70_records_zero_rejections():
    records, rejections = _split(WiselyApiAdapter(DATA_DIR / "wisely_api").extract())
    assert len(records) == 70
    assert rejections == []
    assert len({r.source_key for r in records}) == 13


def test_api_adapter_agravanis_2020_21_spot_check():
    records, _ = _split(WiselyApiAdapter(DATA_DIR / "wisely_api").extract())
    rec = next(r for r in records if r.source_key == "26515" and r.season == "2020-21")
    assert rec.full_name == "Dimitrios Agravanis"
    assert rec.canonical_name == "dimitrios agravanis"
    assert rec.position == "F"
    assert rec.ts_pct_api == pytest.approx(0.569)
    assert rec.usage_pct == pytest.approx(24.8)
    assert rec.gp == 31
    assert rec.source_updated_at is not None


# --- RealGM adapter: real file ----------------------------------------------

def test_realgm_adapter_yields_69_records_2_rejections():
    records, rejections = _split(RealGMAdapter(DATA_DIR / "realgm" / "players.csv").extract())
    assert len(records) == 69
    assert len(rejections) == 2
    reasons = sorted(r.reason for r in rejections)
    assert reasons == [
        "intra-source exact duplicate",
        "intra-source near-duplicate; kept last",
    ]


def test_realgm_adapter_near_duplicate_keeps_last():
    records, _ = _split(RealGMAdapter(DATA_DIR / "realgm" / "players.csv").extract())
    rec = next(
        r for r in records
        if r.canonical_name == "aegir steinarsson" and r.season == "2015-16"
    )
    assert rec.pts_pg == pytest.approx(11.6)  # last row wins, not 11.4


def test_realgm_adapter_empty_cells_become_none():
    records, _ = _split(RealGMAdapter(DATA_DIR / "realgm" / "players.csv").extract())
    agravanis_2020 = next(
        r for r in records
        if r.canonical_name == "dimitrios agravanis" and r.season == "2020-21"
    )
    assert agravanis_2020.age is None

    del_cerro_2023 = next(
        r for r in records
        if r.canonical_name == "adrian del cerro" and r.season == "2023-24"
    )
    assert del_cerro_2023.ast_pg is None


def test_realgm_adapter_agravanis_minutes_parsed_from_mmss():
    records, _ = _split(RealGMAdapter(DATA_DIR / "realgm" / "players.csv").extract())
    rec = next(
        r for r in records
        if r.canonical_name == "dimitrios agravanis" and r.season == "2020-21"
    )
    assert rec.min_pg == pytest.approx(26.6)


def test_realgm_adapter_source_key_is_canonical_name():
    records, _ = _split(RealGMAdapter(DATA_DIR / "realgm" / "players.csv").extract())
    rec = next(r for r in records if r.canonical_name == "adrian del cerro")
    assert rec.source_key == "adrian del cerro"
    assert rec.full_name == "Adrian del Cerro"  # display form, not folded


def test_realgm_adapter_drops_percent_columns():
    records, _ = _split(RealGMAdapter(DATA_DIR / "realgm" / "players.csv").extract())
    for rec in records:
        assert not hasattr(rec, "fg_pct")
        assert not hasattr(rec, "ft_pct")
        assert not hasattr(rec, "tp_pct")


# --- RealGM adapter: synthetic dedup, isolated from real-data drift --------

_CSV_HEADER = "Player,Season,Age,Team,League,GP,MIN,PTS,FGM,FGA,FG%,3PM,3PA,3P%,FTM,FTA,FT%,REB,AST,STL,BLK,TOV\n"


def _write_csv(path: Path, rows: list[str]) -> Path:
    path.write_text(_CSV_HEADER + "\n".join(rows) + "\n")
    return path


def test_synthetic_exact_and_near_duplicate(tmp_path):
    rows = [
        # exact duplicate pair
        "Test Player,2020-21,25,Test Team,Test League,30,20.5,10.0,4.0,8.0,50.0,1.0,2.0,50.0,1.0,1.0,100.0,5.0,2.0,1.0,0.5,1.0",
        "Test Player,2020-21,25,Test Team,Test League,30,20.5,10.0,4.0,8.0,50.0,1.0,2.0,50.0,1.0,1.0,100.0,5.0,2.0,1.0,0.5,1.0",
        # near duplicate pair (different PTS) -> keep last (12.0)
        "Other Player,2021-22,22,Other Team,Other League,28,18.0,9.0,3.5,7.0,50.0,1.0,2.0,50.0,1.0,1.0,100.0,4.0,1.0,1.0,0.0,1.0",
        "Other Player,2021-22,22,Other Team,Other League,28,18.0,12.0,3.5,7.0,50.0,1.0,2.0,50.0,1.0,1.0,100.0,4.0,1.0,1.0,0.0,1.0",
    ]
    csv_path = _write_csv(tmp_path / "synthetic.csv", rows)
    records, rejections = _split(RealGMAdapter(csv_path).extract())

    assert len(records) == 2
    assert len(rejections) == 2
    reasons = {r.reason for r in rejections}
    assert reasons == {"intra-source exact duplicate", "intra-source near-duplicate; kept last"}

    other = next(r for r in records if r.canonical_name == "other player")
    assert other.pts_pg == pytest.approx(12.0)


def test_synthetic_bad_row_isolated(tmp_path):
    rows = [
        "Good Player,2020-21,25,Team,League,30,20.5,10.0,4.0,8.0,50.0,1.0,2.0,50.0,1.0,1.0,100.0,5.0,2.0,1.0,0.5,1.0",
        "Bad Player,2020-21,25,Team,League,30,garbage,10.0,4.0,8.0,50.0,1.0,2.0,50.0,1.0,1.0,100.0,5.0,2.0,1.0,0.5,1.0",
    ]
    csv_path = _write_csv(tmp_path / "synthetic_bad.csv", rows)
    records, rejections = _split(RealGMAdapter(csv_path).extract())

    assert len(records) == 1
    assert records[0].canonical_name == "good player"
    assert len(rejections) == 1
    assert "unparseable minutes" in rejections[0].reason
