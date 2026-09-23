"""데모 화면 — 브라우저 없이(streamlit AppTest) 세 모드가 오류 없이 그려지는지. API 키 없이."""
import json

import pytest
from streamlit.testing.v1 import AppTest

import graph


def saved_runs():
    path = graph.OUTPUT / "runs.jsonl"
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] if path.exists() else []


@pytest.fixture
def app(monkeypatch):
    if not saved_runs():
        pytest.skip("output/runs.jsonl 이 없다")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)     # 키 없이도 열려야 한다
    monkeypatch.setattr(graph, "load_dotenv", lambda *a, **k: None, raising=False)
    at = AppTest.from_file(str(graph.BASE / "app.py"), default_timeout=60)
    return at.run()


def widget(items, key):
    return next(w for w in items if w.key == key)


def test_first_screen_is_ask_with_intro(app):
    assert not app.exception
    mode = widget(app.radio, "mode")
    assert mode.options == ["질문하기", "데모 보기", "질문 선택"] and mode.value == "질문하기"
    text = " ".join(m.value for m in app.markdown)
    assert "노벨 문학상 딥리서처" in text and "무엇을 할까요" in text      # 머리 설명 · 모드 고르기가 첫 화면에
    assert widget(app.text_input, "ask_q")                               # 직접 쓰는 칸


def test_demo_mode_renders_team_and_solo(app):
    widget(app.radio, "mode").set_value("데모 보기").run()
    assert not app.exception
    assert [t.label for t in app.tabs][:4] == ["🔎 절별 작업", "📄 보고서", "📊 지표", "↔ 나란히 보기"]
    runs_box = widget(app.selectbox, "demo_run")
    seen = set()
    for option in runs_box.options:                 # 설정마다(팀 7종 + 혼자) 하나씩 열어 본다
        setting = option.split(" · ")[2]
        if setting in seen:
            continue
        seen.add(setting)
        widget(app.selectbox, "demo_run").set_value(option).run()
        assert not app.exception, option
    assert "혼자" in seen and "기본" in seen
    assert any("기획" in m.value for m in app.markdown)


def test_pick_mode_lists_prepared_questions(app):
    widget(app.radio, "mode").set_value("질문 선택").run()
    assert not app.exception
    assert len(widget(app.selectbox, "pick_q").options) == 10


def test_sidebar_shows_ablation3_line_from_file(app):
    """제출 뒤 ablation-3 한 줄 — 숫자는 output/ablation-3.json 에서 (파일이 없으면 줄도 없다)."""
    path = graph.OUTPUT / "ablation-3.json"
    text = " ".join(m.value for m in app.sidebar.markdown)
    if not path.exists():
        assert "ablation-3" not in text
        return
    q5 = json.loads(path.read_text(encoding="utf-8"))["결과"]["Q5"]
    on, off = (q5[k]["경보합"].get("시대불일치", 0) for k in ("기본", "기획보강끔"))
    assert f"기획보강 켬 {on}건 · 끔 {off}건" in text
