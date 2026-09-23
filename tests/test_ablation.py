"""절제 실험 — 스위치가 로그만 바꾸는 게 아니라 실제 코드 경로를 바꾸는지, 이어 돌리기가 되는지."""
import json

import ablation
import graph
from fakes import FakeLLM

PLAN = {"제목": "t", "목차": [
    {"절": "초기", "지시": "초기", "역할": "생애 담당", "시작문서": "셀마 라겔뢰프",
     "담당문서": ["셀마 라겔뢰프", "그라치아 델레다"], "비중": 2},
    {"절": "최근", "지시": "최근", "역할": "작품 담당", "시작문서": "한강 (작가)",
     "담당문서": ["한강 (작가)", "채식주의자 (소설)"], "비중": 2}]}
WEAK = {"본문": "짧다 «셀마 라겔뢰프». " * 10, "충분": False, "부족": "더 필요"}


def run_with(monkeypatch, picks=(), **switches):
    fake = FakeLLM(plan=PLAN, write={"초기": [WEAK]}, picks=picks)
    monkeypatch.setattr(graph, "_invoke", fake)
    return graph.run("여성 수상자", **switches), fake


def test_every_switch_changes_the_code_path(monkeypatch):
    base, base_fake = run_with(monkeypatch)
    # 역할 끔 — 모든 절이 기본역할
    out, _ = run_with(monkeypatch, 역할=False)
    assert {t["역할"] for t in out["plan"]["목차"]} == {graph.CONFIG["기본역할"]}
    assert {t["역할"] for t in base["plan"]["목차"]} == {"생애 담당", "작품 담당"}
    # 배정 끔 — 시작·담당문서가 없고, 읽은 문서는 전부 서브에이전트가 모델에게 물어 고른 것이다
    out, _ = run_with(monkeypatch, picks=["토니 모리슨"], 배정=False)
    assert all(not t["시작문서"] and not t["담당문서"] for t in out["plan"]["목차"])
    read = {d for s in out["sections"] for d in s["읽은문서"]}
    assert read == {"토니 모리슨"}                                  # 배정된 문서는 하나도 없다
    base_read = {d for s in base["sections"] for d in s["읽은문서"]}
    assert {"셀마 라겔뢰프", "한강 (작가)"} <= base_read            # 기본은 배정 문서를 모델에게 묻지 않고 읽는다
    # 구역 끔 — 피하기 목록이 비어서 나간다
    out, _ = run_with(monkeypatch, 구역=False)
    assert all(s["피하기"] == [] for s in out["sections"])
    assert any(s["피하기"] for s in base["sections"])
    # 기본(담당구역 끔, 수업 방식) — 남의 시작문서만 피하고 담당문서는 피하기에 없다
    base_first = next(s for s in base["sections"] if s["절"] == "초기" and s["바퀴"] == 1)
    assert "한강 (작가)" in base_first["피하기"] and "채식주의자 (소설)" not in base_first["피하기"]
    # 담당구역 켬 — 남의 담당문서까지 피한다
    out, _ = run_with(monkeypatch, 담당구역=True)
    first = next(s for s in out["sections"] if s["절"] == "초기" and s["바퀴"] == 1)
    assert "채식주의자 (소설)" in first["피하기"]
    # 재촉 — 배정이 없을 때 모델이 그만두려 해도 예산을 쓰도록 붙잡는다
    out, fake = run_with(monkeypatch, 배정=False, 재촉=True)
    read_nudged = sum(s["이번_읽은글자"] for s in out["sections"])
    out, _ = run_with(monkeypatch, 배정=False)
    read_plain = sum(s["이번_읽은글자"] for s in out["sections"])
    assert read_plain == 0 < read_nudged                       # 가짜 모델은 늘 '그만'이라 한다
    assert fake.stage_count("[알림]") == 0                      # (알림은 사용자 글에 붙는다 — 아래에서 확인)
    assert any("[알림]" in u for _, u in fake.calls)
    # 재위임 끔 — 부족 신고가 있어도 2바퀴가 없다
    out, _ = run_with(monkeypatch, 재위임=False)
    assert {s["바퀴"] for s in out["sections"]} == {1}
    assert 2 in {s["바퀴"] for s in base["sections"]}


def test_ablation_resumes_and_summarizes(monkeypatch, tmp_path):
    monkeypatch.setattr(graph, "_invoke", FakeLLM(plan=PLAN, solo_start=["셀마 라겔뢰프"]))
    monkeypatch.setattr(graph, "OUTPUT", tmp_path)
    monkeypatch.setattr(ablation, "RUNS", tmp_path / "runs.jsonl")
    monkeypatch.setattr(ablation, "OUT", tmp_path / "ablation.json")
    ablation.run_question("t1", "Q5", repeats=2, settings=["기본", "재위임끔", "혼자"])
    rows = ablation.load_runs("t1")
    assert sorted(ablation.done_key(r) for r in rows) == sorted(
        ("Q5", s, r) for s in ("기본", "재위임끔", "혼자") for r in (1, 2))
    # 혼자는 같은 회차 기본 실행이 읽은 만큼 예산을 받았다
    for rep in (1, 2):
        base = next(r for r in rows if r["설정"] == "기본" and r["반복"] == rep)
        solo = next(r for r in rows if r["설정"] == "혼자" and r["반복"] == rep)
        assert solo["metrics"]["_짝"] == base["id"]
        assert sum(v[2] for v in solo["visited"]) <= sum(v[2] for v in base["visited"])
    # 다시 부르면 끝난 것은 건너뛴다
    ablation.run_question("t1", "Q5", repeats=2, settings=["기본", "재위임끔", "혼자"])
    assert len(ablation.load_runs("t1")) == 6
    summary = ablation.summarize("t1")
    e = summary["Q5"]["기본"]
    assert e["n"] == 2 and len(e["근거율"]["값"]) == 2 and "표준편차" in e["근거율"]
    saved = json.loads((tmp_path / "ablation.json").read_text(encoding="utf-8"))
    assert saved["실험"] == "t1" and "Q5" in saved["결과"]
