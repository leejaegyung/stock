"""
Layer 6 — CLI entrypoint.
Usage: python -m app.entrypoints.cli <command>
"""

import json
import logging
from datetime import date

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.core.pipeline import analyze_stock, morning_brief
from app.db.client import get_session_factory
from app.db.models import AnalysisReport, Watchlist, create_all_tables

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

app = typer.Typer(help="주식 분석 서비스 CLI")
console = Console()


def _session():
    create_all_tables(settings.db_path)
    factory = get_session_factory(settings.db_path)
    return factory()


@app.command()
def add(
    ticker: str = typer.Argument(..., help="종목 티커 (예: NVDA, 005930)"),
    market: str = typer.Option(..., help="KR 또는 US"),
    name: str = typer.Option(..., help="회사명"),
):
    """관심종목에 추가."""
    with _session() as session:
        try:
            session.add(Watchlist(ticker=ticker.upper(), name=name, market=market.upper()))
            session.commit()
            console.print(f"[green]✓ {ticker} [{market}] '{name}' 추가됨[/green]")
        except IntegrityError:
            console.print(f"[yellow]이미 존재합니다: {ticker} [{market}][/yellow]")


@app.command()
def remove(
    ticker: str = typer.Argument(...),
    market: str = typer.Option(..., help="KR 또는 US"),
):
    """관심종목에서 삭제."""
    with _session() as session:
        row = (
            session.query(Watchlist)
            .filter_by(ticker=ticker.upper(), market=market.upper())
            .first()
        )
        if row:
            session.delete(row)
            session.commit()
            console.print(f"[green]✓ {ticker} [{market}] 삭제됨[/green]")
        else:
            console.print(f"[red]찾을 수 없음: {ticker} [{market}][/red]")


@app.command(name="list")
def list_watchlist():
    """관심종목 목록 출력."""
    with _session() as session:
        rows = session.query(Watchlist).all()
        if not rows:
            console.print("관심종목이 없습니다. 'add' 명령으로 추가하세요.")
            return
        table = Table(title="관심종목")
        table.add_column("티커"), table.add_column("시장"), table.add_column("이름"), table.add_column("추가일")
        for r in rows:
            table.add_row(r.ticker, r.market, r.name, str(r.added_at)[:10])
        console.print(table)


@app.command()
def analyze(
    ticker: str = typer.Argument(...),
    market: str = typer.Option("US", help="KR 또는 US"),
    date_str: str = typer.Option(None, "--date", help="YYYY-MM-DD (기본: 오늘)"),
):
    """단일 종목 전체 분석 실행."""
    from anthropic import Anthropic
    if date_str is None:
        date_str = date.today().isoformat()

    console.print(f"[bold]분석 시작: {ticker} [{market}] ({date_str})[/bold]")
    client = Anthropic(api_key=settings.anthropic_api_key)

    result = analyze_stock(ticker.upper(), market.upper(), date_str, client)
    advice = result.get("advice", {})
    verdict = advice.get("verdict", "보류")
    confidence = advice.get("confidence", "-")

    brief_section = advice.get("brief_section", "")
    console.print(brief_section)
    console.print(f"\n[bold]결론: {verdict} (확신도: {confidence})[/bold]")

    with _session() as session:
        session.add(AnalysisReport(
            ticker=ticker.upper(),
            market=market.upper(),
            date=date_str,
            verdict=verdict,
            confidence=confidence,
            report_md=brief_section,
        ))
        session.commit()


@app.command()
def brief(date_str: str = typer.Option(None, "--date")):
    """전체 watchlist 아침 브리핑 실행."""
    if date_str is None:
        date_str = date.today().isoformat()

    with _session() as session:
        rows = session.query(Watchlist).all()
        watchlist = [{"ticker": r.ticker, "market": r.market, "name": r.name} for r in rows]

    if not watchlist:
        console.print("[yellow]관심종목이 없습니다.[/yellow]")
        return

    console.print(f"[bold]아침 브리핑 시작 ({date_str}) — {len(watchlist)}개 종목[/bold]")
    report_md = morning_brief(watchlist, date_str)
    console.print(report_md)

    with _session() as session:
        session.add(AnalysisReport(
            ticker="_BRIEF_",
            market="ALL",
            date=date_str,
            verdict="브리핑",
            confidence="-",
            report_md=report_md,
        ))
        session.commit()


if __name__ == "__main__":
    app()
