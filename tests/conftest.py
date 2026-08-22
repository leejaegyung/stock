import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.models import Base


@pytest.fixture
def anthropic_client():
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        pytest.skip("ANTHROPIC_API_KEY not set — skipping live test")
    from anthropic import Anthropic
    return Anthropic(api_key=api_key)


@pytest.fixture
def sample_financial_data() -> dict:
    return {
        "ticker": "NVDA",
        "market": "US",
        "info": {
            "shortName": "NVIDIA Corporation",
            "sector": "Technology",
            "trailingPE": 45.0,
            "returnOnEquity": 0.85,
            "debtToEquity": 40.0,
            "dividendYield": 0.003,
            "earningsGrowth": 0.30,
        },
        "cashflow": {"operating": 15_000_000_000, "investing": -3_000_000_000, "financing": -5_000_000_000},
        "balance_sheet": {},
    }


@pytest.fixture
def in_memory_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()
