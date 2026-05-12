"""Shared pytest fixtures.

Head-only XLSX samples live under tests/fixtures/. The samples are
produced by truncating the data sheet of each real APRA file to ~80–200
rows so the unit suite runs fast (and so we don't ship the 7MB
historical GI file inside the repo).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from apra_mcp import curated


FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def reset_curated_registry():
    curated.reset_registry()
    yield
    curated.reset_registry()


@pytest.fixture
def fixture_dir() -> Path:
    return FIXTURE_DIR


@pytest.fixture
def adi_key_stats_xlsx() -> bytes:
    return (FIXTURE_DIR / "adi_key_stats_sample.xlsx").read_bytes()


@pytest.fixture
def adi_rwa_xlsx() -> bytes:
    return (FIXTURE_DIR / "adi_rwa_sample.xlsx").read_bytes()


@pytest.fixture
def super_fund_level_xlsx() -> bytes:
    return (FIXTURE_DIR / "super_fund_level_sample.xlsx").read_bytes()


@pytest.fixture
def insurance_general_xlsx() -> bytes:
    return (FIXTURE_DIR / "insurance_general_sample.xlsx").read_bytes()


@pytest.fixture
def insurance_general_historical_xlsx() -> bytes:
    return (FIXTURE_DIR / "insurance_general_historical_sample.xlsx").read_bytes()


@pytest.fixture
def life_insurance_xlsx() -> bytes:
    return (FIXTURE_DIR / "life_insurance_sample.xlsx").read_bytes()


@pytest.fixture
def life_insurance_historical_xlsx() -> bytes:
    return (FIXTURE_DIR / "life_insurance_historical_sample.xlsx").read_bytes()
