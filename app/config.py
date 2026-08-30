from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    anthropic_api_key: str = ""
    db_path: str = "data/stock_analyst.db"
    haiku_model: str = "claude-haiku-4-5-20251001"
    sonnet_model: str = "claude-sonnet-4-6"
    opus_model: str = "claude-opus-4-8"
    brief_cron: str = "30 10 * * *"
    dart_api_key: str = ""   # 선택: opendart.fss.or.kr — KR 재무제표 활성화
    fred_api_key: str = ""   # 선택: fred.stlouisfed.org — 미국 Fed금리·CPI·실업률 실데이터
    ecos_api_key: str = ""   # 선택: ecos.bok.or.kr — 한국 기준금리·CPI 실데이터
    deepl_api_key: str = ""  # 선택: deepl.com/pro-api — 뉴스 번역 품질·안정성 향상 (LLM 아님, NMT)

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
