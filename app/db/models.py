from datetime import datetime, timezone

from sqlalchemy import BigInteger, Column, DateTime, Float, Integer, String, Text, UniqueConstraint, update
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Watchlist(Base):
    __tablename__ = "watchlist"
    __table_args__ = (UniqueConstraint("ticker", "market"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String, nullable=False)
    name = Column(String, nullable=False)
    market = Column(String, nullable=False)  # 'US' | 'KR'
    added_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    quantity = Column(Float, default=0.0)    # 보유 수량
    avg_price = Column(Float, default=0.0)   # 평균 매입가 (현지통화)


class AnalysisReport(Base):
    __tablename__ = "analysis_report"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String, nullable=False)
    market = Column(String, nullable=False)
    date = Column(String, nullable=False)
    verdict = Column(String)
    confidence = Column(String)
    report_md = Column(Text)
    metrics_json = Column(Text, default="")   # 구조화 지표(점수·EV·켈리·신호 등) JSON
    source = Column(String, default="watchlist")  # watchlist | discovered (스캐너 자동발굴)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class NewsItem(Base):
    __tablename__ = "news_item"
    __table_args__ = (UniqueConstraint("url_hash"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String, nullable=False)
    market = Column(String, nullable=False)
    headline = Column(String)
    summary = Column(Text)
    headline_ko = Column(String)      # 자동 번역된 헤드라인 (외신만)
    summary_ko = Column(Text)         # 자동 번역된 요약
    lang = Column(String)             # 'ko' | 'en' | 'unknown' — 원문 언어
    impact = Column(String)  # '촉매' | '중립' | '저해'
    source = Column(String)
    url = Column(String, default="")
    published_at = Column(DateTime)
    url_hash = Column(String, unique=True)


class LedgerTransaction(Base):
    """가계부 거래 항목."""
    __tablename__ = "ledger_transaction"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String, nullable=False)           # YYYY-MM-DD
    type = Column(String, nullable=False)           # '수입' | '지출'
    category = Column(String, nullable=False)
    amount = Column(BigInteger, nullable=False)     # 원 단위
    memo = Column(String, default="")
    source_recurring_id = Column(Integer, default=None)  # 고정거래에서 생성 시 참조 ID
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class RecurringTransaction(Base):
    """고정 수입/지출 템플릿 — 매달 자동 등록."""
    __tablename__ = "recurring_transaction"

    id = Column(Integer, primary_key=True, autoincrement=True)
    type = Column(String, nullable=False)          # '수입' | '지출'
    category = Column(String, nullable=False)
    amount = Column(BigInteger, nullable=False)    # 원 단위
    memo = Column(String, default="")
    day_of_month = Column(Integer, default=1)      # 매달 적용 일자 (1~28)
    is_active = Column(Integer, default=1)         # 1=활성, 0=비활성
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class AssetItem(Base):
    """자산 항목."""
    __tablename__ = "asset_item"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    asset_type = Column(String, nullable=False)     # 현금|주식|예금|부동산|기타
    amount = Column(BigInteger, nullable=False)     # 원 단위
    note = Column(String, default="")
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class NetWorthSnapshot(Base):
    """일별 순자산 스냅샷 — 추이 차트용. 하루 1건(upsert)."""
    __tablename__ = "net_worth_snapshot"
    __table_args__ = (UniqueConstraint("date"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String, nullable=False)          # YYYY-MM-DD
    total_assets = Column(BigInteger, default=0)   # 등록 자산 합계 (원)
    stock_value = Column(BigInteger, default=0)    # 주식 평가금액 (원)
    stock_cost = Column(BigInteger, default=0)     # 주식 매입금액 (원)
    net_worth = Column(BigInteger, default=0)      # total_assets + stock_value
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class UserApiKey(Base):
    """사용자별 LLM API 키 저장 (추후 로그인 연동 대비)."""
    __tablename__ = "user_api_key"
    __table_args__ = (UniqueConstraint("user_id", "provider"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, nullable=False, default="default")   # 현재는 항상 "default"
    provider = Column(String, nullable=False)                      # 'anthropic' | 'openai'
    api_key = Column(String, nullable=False)
    is_active = Column(Integer, default=0)                         # 1=활성, 0=비활성
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class PaperAccount(Base):
    """모의투자 계좌 — 실제 돈이 오가지 않는 가상 현금 잔고 (단일 계좌, id=1 고정 사용)."""
    __tablename__ = "paper_account"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cash_krw = Column(Float, default=10_000_000.0)          # 현재 가상 현금 (원)
    initial_cash_krw = Column(Float, default=10_000_000.0)  # 시작 시드 (리셋 기준)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class PaperTrade(Base):
    """모의투자 라운드트립 거래 1건 — 진입 시 open 으로 생성, 청산되면 closed 로 갱신.
    우리 알고리즘(analyze_stock_algo)의 결론·매매 타이밍을 그대로 따라간 가상 체결이며,
    이 이력이 쌓여 신호 적중률·확신도 모델을 검증·개선하는 학습 데이터가 된다."""
    __tablename__ = "paper_trade"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String, nullable=False)
    market = Column(String, nullable=False)
    quantity = Column(Float, nullable=False)
    entry_source = Column(String, default="watchlist")       # watchlist | discovered (AI 발굴)

    entry_date = Column(String, nullable=False)             # YYYY-MM-DD
    entry_price = Column(Float, nullable=False)              # 현지통화 1주당
    entry_price_krw = Column(Float, nullable=False)          # 원화 환산 1주당
    entry_report_id = Column(Integer)                        # 근거 AnalysisReport.id
    entry_verdict = Column(String)
    entry_confidence_score = Column(Integer)
    entry_confidence_grade = Column(String)
    target1 = Column(Float)
    target2 = Column(Float)
    stop = Column(Float)

    exit_date = Column(String)
    exit_price = Column(Float)
    exit_price_krw = Column(Float)
    exit_reason = Column(String)   # target1 | target2 | stop | verdict_sell | timeout | manual

    pnl_krw = Column(Float)
    pnl_pct = Column(Float)
    status = Column(String, default="open", nullable=False)  # open | closed

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


def create_all_tables(db_path: str) -> None:
    import os
    from app.db.client import get_engine

    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    engine = get_engine(db_path)
    Base.metadata.create_all(engine)

    # 기존 DB 마이그레이션 (컬럼 추가)
    with engine.connect() as conn:
        from sqlalchemy import text
        migrations = [
            "ALTER TABLE news_item ADD COLUMN url TEXT DEFAULT ''",
            "ALTER TABLE watchlist ADD COLUMN quantity REAL DEFAULT 0",
            "ALTER TABLE watchlist ADD COLUMN avg_price REAL DEFAULT 0",
            "ALTER TABLE user_api_key ADD COLUMN is_active INTEGER DEFAULT 0",
            "ALTER TABLE ledger_transaction ADD COLUMN source_recurring_id INTEGER DEFAULT NULL",
            "ALTER TABLE analysis_report ADD COLUMN metrics_json TEXT DEFAULT ''",
            "ALTER TABLE news_item ADD COLUMN headline_ko TEXT",
            "ALTER TABLE news_item ADD COLUMN summary_ko TEXT",
            "ALTER TABLE news_item ADD COLUMN lang TEXT",
            "ALTER TABLE analysis_report ADD COLUMN source TEXT DEFAULT 'watchlist'",
            "ALTER TABLE paper_trade ADD COLUMN entry_source TEXT DEFAULT 'watchlist'",
        ]
        for sql in migrations:
            try:
                conn.execute(text(sql))
                conn.commit()
            except Exception:
                pass  # 이미 존재하면 무시
