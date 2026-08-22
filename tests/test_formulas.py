import math

import pytest

from app.core.formulas import (
    cape_judgment,
    cashflow_pattern,
    expected_value,
    half_kelly,
    kelly_fraction,
    per_roe_judgment,
    peg_ratio,
)


# ── Expected Value ─────────────────────────────────────────────────────────────

def test_expected_value_positive():
    ev = expected_value(win_prob=0.6, gain=100, loss=50)
    assert ev > 0
    assert abs(ev - (0.6 * 100 - 0.4 * 50)) < 1e-9


def test_expected_value_negative():
    ev = expected_value(win_prob=0.3, gain=50, loss=100)
    assert ev < 0


def test_expected_value_breakeven():
    ev = expected_value(win_prob=0.5, gain=100, loss=100)
    assert abs(ev) < 1e-9


# ── Kelly ──────────────────────────────────────────────────────────────────────

def test_kelly_fraction_positive():
    # 60% win, 2:1 odds → kelly = 0.6 - 0.4/2 = 0.4
    k = kelly_fraction(0.6, 2.0)
    assert abs(k - 0.4) < 1e-9


def test_kelly_fraction_zero_odds():
    k = kelly_fraction(0.6, 0.0)
    assert k == 0.0


def test_kelly_fraction_do_not_enter():
    # Poor odds → negative kelly = do not enter
    k = kelly_fraction(0.2, 0.5)
    assert k < 0


def test_half_kelly():
    hk = half_kelly(0.6, 2.0)
    full = kelly_fraction(0.6, 2.0)
    assert abs(hk - full / 2) < 1e-9


# ── Cashflow Pattern ──────────────────────────────────────────────────────────

def test_cashflow_pattern_all_cases():
    assert cashflow_pattern(100, -50, -30) == "우량 성숙기"
    assert cashflow_pattern(100, -50, 30) == "성장기"
    assert cashflow_pattern(100, 50, -30) == "구조조정 신호"
    assert cashflow_pattern(-100, 50, 30) == "위험"


def test_cashflow_pattern_zero_boundary():
    # Zero is treated as non-negative (>=0)
    result = cashflow_pattern(0, -1, -1)
    assert result == "우량 성숙기"


# ── CAPE Judgment ─────────────────────────────────────────────────────────────

def test_cape_judgment_thresholds():
    assert cape_judgment(10.0) == "저평가"
    assert cape_judgment(20.0) == "적정"
    assert cape_judgment(30.0) == "고평가"
    assert cape_judgment(40.0) == "버블"


def test_cape_judgment_boundary():
    assert cape_judgment(14.9) == "저평가"
    assert cape_judgment(15.0) == "적정"
    assert cape_judgment(24.9) == "적정"
    assert cape_judgment(25.0) == "고평가"
    assert cape_judgment(34.9) == "고평가"
    assert cape_judgment(35.0) == "버블"


# ── PER/ROE Judgment ──────────────────────────────────────────────────────────

def test_per_roe_judgment_ideal():
    result = per_roe_judgment(per=10, roe=0.20, sector_avg_per=20, sector_avg_roe=0.10)
    assert "저평가 우량주" in result


def test_per_roe_judgment_expensive_quality():
    result = per_roe_judgment(per=30, roe=0.25, sector_avg_per=20, sector_avg_roe=0.10)
    assert "고PER" in result and "고ROE" in result


def test_per_roe_judgment_cheap_mediocre():
    result = per_roe_judgment(per=10, roe=0.05, sector_avg_per=20, sector_avg_roe=0.10)
    assert "저PER" in result and "저ROE" in result


def test_per_roe_judgment_avoid():
    result = per_roe_judgment(per=30, roe=0.05, sector_avg_per=20, sector_avg_roe=0.10)
    assert "주의" in result


# ── PEG Ratio ─────────────────────────────────────────────────────────────────

def test_peg_ratio_normal():
    assert abs(peg_ratio(30, 15) - 2.0) < 1e-9


def test_peg_ratio_zero_growth():
    assert peg_ratio(30, 0) == float("inf")


def test_peg_ratio_negative_growth():
    assert peg_ratio(30, -5) == float("inf")
