"""지표 — 고정 입력으로 값이 정확히 나오는지, 그리고 규칙이 조용히 바뀌지 않았는지."""
import json

import graph
import metrics
from fakes import FakeLLM

CORPUS = {"docs": {"A": "a" * 1000, "B": "b" * 1000, "C": "c" * 1000, "공용": "x" * 1000},
          "_종류": {"A": "수상자", "B": "수상자", "C": "작품", "공용": "공통"}}


def test_sentence_rule_is_frozen():
    """문장 나누기 규칙이 바뀌면 근거율이 바뀐다 — 규칙을 고치면 이 테스트도 의식적으로 고친다."""
    text = "# 제목\n\n첫 문장은 충분히 길게 쓴 문장이다. «A» 둘째 문장도 충분히 길게 썼다«B».\n짧다."
    assert metrics.sentences(text) == ["첫 문장은 충분히 길게 쓴 문장이다«A».", "둘째 문장도 충분히 길게 썼다«B»."]


def run_record(report, visited, sections, cost=(), plan=None):
    return {"report": report, "visited": visited, "sections": sections, "cost": list(cost),
            "plan": plan or {"교정": []}}


def test_grounding_counts_only_read_docs():
    report = "이것은 첫 번째 긴 문장이다 «A». 이것은 두 번째 긴 문장이다 «C». 근거 없는 세 번째 긴 문장이다."
    visited = [["절1", "A", 800, 1]]
    m, d = metrics.measure(run_record(report, visited, [{"인용": ["A"], "예산": 1000, "본문": "x" * 200}]), CORPUS)
    assert d["문장수"] == 3 and d["근거붙은문장"] == 1          # «C» 는 읽지 않았으므로 근거가 아니다
    assert m["근거율"] == 33.3
    assert m["예산소진율"] == 80.0


def test_overlap_ignores_shared_shelf():
    visited = [["절1", "A", 100, 1], ["절1", "공용", 100, 1], ["절2", "A", 100, 1],
               ["절2", "공용", 100, 1], ["절2", "B", 100, 1]]
    m, d = metrics.measure(run_record("", visited, []), CORPUS)
    assert d["절간중복문서"] == ["A"]                        # 공용은 겹쳐도 경보가 아니다
    assert m["절간중복문서"] == 1
    assert m["중복률"] == round(100 * (1 - 2 / 3), 1)       # (절1,A)(절2,A)(절2,B) 중 서로 다른 문서 2


def test_isolation_numbers_are_reported_separately():
    cost = [{"누가": "코디", "단계": "기획", "글자": 400, "절": ""},
            {"누가": "서브", "단계": "읽기", "글자": 600, "절": "절1"}]
    m, _ = metrics.measure(run_record("", [], [], cost), CORPUS)
    assert m["격리율"] == 40.0                               # 수업 정의
    assert m["코디카드열람률"] == 10.0                        # 400 / 코퍼스 4,000자


def test_alarms_pick_only_nonzero_alarms():
    sections = [{"인용": [], "허위인용": ["B"], "없는문서인용": [], "본문": "짧다", "예산": 1}]
    plan = {"교정": ["«x» 담당문서 'A' 가 다른 절과 겹쳐 뺌 (구역 겹침)", "«y» 시작문서 'Z' 가 코퍼스에 없어 뺌"]}
    m, _ = metrics.measure(run_record("", [], sections, plan=plan), CORPUS)
    a = metrics.alarms(m)
    assert a == {"허위인용": 1, "인용0곳절": 1, "빈원고절": 1, "구역겹침": 1, "배정실패": 1}
    assert all(metrics.METRICS[k][0] == "경보" for k in a)


def test_era_mismatch_alarm_counts_the_final_plan():
    """교정 기록이 아니라 최종 목차에서 센다 — 그래야 검사가 없던 예전 실행을 다시 재도 놓친 건수가 나온다."""
    toc = [{"절": "중기 (1951-2000)", "담당문서": ["토니 모리슨", "도리스 레싱", "엘프리데 옐리네크"]},
           {"절": "최근 수상자", "담당문서": ["한강 (작가)"]}]                    # 기간이 없는 절은 보지 않는다
    m, _ = metrics.measure(run_record("", [], [], plan={"교정": [], "목차": toc}), CORPUS)
    assert m["시대불일치"] == 2
    assert metrics.alarms(m) == {"시대불일치": 2}


def test_every_metric_is_documented():
    m, _ = metrics.measure(run_record("", [], []), CORPUS)
    assert set(m) == set(metrics.METRICS)
    for name, (kind, device, desc) in metrics.METRICS.items():
        assert kind in ("신호", "경보") and device and desc and "\n" not in desc


def test_full_run_is_measured_and_saved(monkeypatch, tmp_path):
    plan = {"제목": "t", "목차": [
        {"절": "초기", "지시": "x", "역할": "생애 담당", "시작문서": "셀마 라겔뢰프", "담당문서": ["그라치아 델레다"], "비중": 2},
        {"절": "최근", "지시": "x", "역할": "작품 담당", "시작문서": "한강 (작가)", "담당문서": [], "비중": 2}]}
    monkeypatch.setattr(graph, "_invoke", FakeLLM(plan=plan))
    monkeypatch.setattr(graph, "OUTPUT", tmp_path)
    out = graph.run("여성 수상자")
    assert out["report"].startswith("# t") and "## 1." in out["report"]
    assert out["metrics"]["허위인용"] == 0 and out["metrics"]["근거율"] > 0
    assert any(c["단계"] == "종합" and c["누가"] == "코디" for c in out["cost"])
    run_id, path = graph.save_run(out, "Q5")
    rec = json.loads((tmp_path / "runs.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert rec["id"] == run_id and path.exists()
    assert rec["코퍼스"]["문서수"] == len(graph.DOCS)
    assert {"스위치", "config", "sections", "원고전부", "visited", "cost", "metrics"} <= set(rec)
    # 저장된 기록만으로 지표를 다시 계산하면 같은 값이 나온다 (데모·ablation 이 기대는 성질)
    again, _ = metrics.measure({**rec, "all_drafts": rec["원고전부"]}, graph.CORPUS)
    assert again == {k: v for k, v in out["metrics"].items() if k != "_세부"}
