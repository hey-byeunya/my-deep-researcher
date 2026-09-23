"""2026-09-23 코드 리뷰에서 고친 것들 — 다시 깨지지 않게."""
import json

import pytest

import ablation
import baseline
import compare
import graph
import metrics
import titles
from fakes import FakeLLM

NESTED = {"제목": "t", "목차": [{"절": "a", "담당문서": ["x"]}]}


# ─── High 1 · jload ─────────────────────────────────────

@pytest.mark.parametrize("raw", [
    json.dumps(NESTED, ensure_ascii=False),                                  # 그대로
    "목차는 다음과 같다: " + json.dumps(NESTED, ensure_ascii=False) + " 끝.",   # 설명이 섞임
    json.dumps(NESTED, ensure_ascii=False) + '\n덧붙임 {"x": 1}',            # JSON 이 둘
    "```json\n" + json.dumps(NESTED, ensure_ascii=False) + "\n```",          # 코드 펜스
])
def test_jload_reads_nested_json_whole(raw):
    assert graph.jload(raw, {}) == NESTED


def test_jload_skips_broken_braces_and_falls_back():
    assert graph.jload("{깨진 것} 다음 " + '{"a": [1, 2]}', {}) == {"a": [1, 2]}
    assert graph.jload("JSON 없음", {"기본": True}) == {"기본": True}
    assert graph.jload('앞 [1, 2] 뒤', []) == [1, 2]


# ─── High 2 · 제목 맞추기 규칙이 한 곳 ─────────────────────

def test_graph_and_metrics_share_title_rules():
    assert graph.resolve_title is titles.resolve_title
    for name, want in [("한강", "한강 (작가)"), ("셀마 라겔뢰프 (1909년 수상)", "셀마 라겔뢰프"), ("«데미안»", "데미안")]:
        assert titles.resolve_title(name, graph.DOCS) == want


def test_metrics_counts_bundled_citations():
    """«A, B» 로 묶인 근거를 graph 도 metrics 도 두 건으로 센다."""
    corpus = {"docs": {"A": "a" * 100, "B": "b" * 100}, "_종류": {}}
    report = "두 문서를 함께 근거로 댄 긴 문장이다 «A, B»."
    m, d = metrics.measure({"report": report, "visited": [["절", "A", 10, 1]], "sections": [], "cost": [],
                            "plan": {}}, corpus)
    assert m["근거율"] == 100.0 and d["인용수"] == 2
    assert graph.check_citations(report, {"A"})["인용"] == []            # 코퍼스가 달라 graph 쪽은 따로 확인
    assert titles.split_refs("셀마 라겔뢰프, 그라치아 델레다") == ["셀마 라겔뢰프", "그라치아 델레다"]


# ─── Medium 7 · 대조군 비용을 바꿔치기하지 않고 who 로 넘긴다 ─────────

def test_read_chunk_records_who(monkeypatch):
    monkeypatch.setattr(graph, "_invoke", FakeLLM())
    costs = []
    graph.read_chunk({"역할": "단독 연구자", "지시": "x", "절": "(혼자)", "역할설명": "혼자 한다"},
                     "셀마 라겔뢰프", 0, 500, costs, who="코디")
    assert costs[-1]["누가"] == "코디"
    out = baseline.solo("여성 수상자", budget=1000, n_sections=1)
    assert {c["누가"] for c in out["cost"]} == {"코디"}


# ─── Medium 8 · 비용 기록에 단계가 없어도 지표가 돈다 ─────────────────

def test_metrics_tolerates_cost_without_stage():
    m, _ = metrics.measure({"report": "", "visited": [], "sections": [], "plan": {},
                            "cost": [{"누가": "코디", "글자": 10}]}, {"docs": {"A": "a"}, "_종류": {}})
    assert m["재기획"] == 0


# ─── Low 12 · 14 · 3 ───────────────────────────────────

def test_compare_exits_with_message_when_no_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(graph, "OUTPUT", tmp_path)
    monkeypatch.setattr("sys.argv", ["compare.py", "--question", "Q5"])
    with pytest.raises(SystemExit) as e:
        compare.main()
    assert "runs.jsonl" in str(e.value)


def test_women_count_uses_list_length():
    assert len(compare.WOMEN) == 18


# ─── 지표 다시 재기 ───────────────────────────────────

def test_remeasure_rewrites_metrics_but_keeps_pairing(tmp_path):
    rec = {"id": "x", "report": "두 문서를 댄 충분히 긴 문장이다 «셀마 라겔뢰프, 그라치아 델레다».",
           "visited": [["절", "셀마 라겔뢰프", 100, 1]], "sections": [], "원고전부": [], "cost": [], "plan": {},
           "metrics": {"근거율": 0.0, "_짝": "팀-실행"}}
    path = tmp_path / "runs.jsonl"
    path.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
    n, changed = ablation.remeasure(path)
    after = json.loads(path.read_text(encoding="utf-8"))
    assert (n, changed) == (1, 1)
    assert after["metrics"]["근거율"] == 100.0 and after["metrics"]["_짝"] == "팀-실행"
    assert after["report"] == rec["report"]                             # 기록은 그대로
