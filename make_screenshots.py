#!/usr/bin/env python3
"""데모 화면 캡처 — README · REPORT 에 넣을 그림을 매번 같은 방식으로 만든다.

먼저 데모를 띄워 두고 (다른 터미널에서):
    .venv/bin/streamlit run app.py --server.port 8504

그리고:
    .venv/bin/pip install playwright && .venv/bin/playwright install chromium   # 이 스크립트에만 필요
    .venv/bin/python make_screenshots.py

첫 화면을 한 장 찍고, '데모 보기'로 저장된 실행(Q5 · 팀 기본)을 열어 탭마다 한 장씩 찍는다. API 키는 필요 없다.
"""
import os
from pathlib import Path

URL = os.environ.get("DEMO_URL", "http://localhost:8504")
OUT = Path(__file__).parent / "docs" / "screenshots"

HOME = ("demo-home.png", "첫 화면 — 설명 · 무엇을 할까요 · 질문하기")
SHOTS = [                       # (파일 이름, 누를 탭, 설명) — '데모 보기' 에서 저장된 실행(Q5 · 팀 기본)으로
    ("demo-work.png", "절별 작업", "절마다 누가 무엇을 읽고 무엇을 썼나"),
    ("demo-report.png", "보고서", "최종 보고서 — «근거» 칩"),
    ("demo-metrics.png", "지표", "신호 · 경보 · 격리 숫자 셋"),
    ("demo-side.png", "나란히 보기", "팀 대 혼자"),
]


def main():
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1100}, device_scale_factor=2)
        page.goto(URL)
        page.get_by_text("무엇을 할까요").first.wait_for(timeout=60000)
        page.wait_for_timeout(1500)
        page.screenshot(path=str(OUT / HOME[0]), full_page=False)
        print(f"저장: docs/screenshots/{HOME[0]} — {HOME[1]}")
        page.get_by_text("데모 보기", exact=True).first.click()
        page.get_by_text("절별 작업").first.wait_for(timeout=60000)
        page.wait_for_timeout(2000)
        for name, tab, desc in SHOTS:
            page.get_by_role("tab", name=tab).click()
            page.wait_for_timeout(1500)
            page.screenshot(path=str(OUT / name), full_page=False)
            print(f"저장: docs/screenshots/{name} — {desc}")
        browser.close()


if __name__ == "__main__":
    main()
