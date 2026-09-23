"""혼자 하는 대조군 — 같은 예산을 끝까지 쓰는지, 팀과 같은 규칙으로 재는지."""
import baseline
import graph
import metrics
from fakes import FakeLLM


def test_solo_spends_whole_budget_even_if_model_wants_to_stop(monkeypatch):
    # 모델은 다음 문서를 고르지 않는다(null) — 그래도 예산은 끝까지 쓴다
    fake = FakeLLM(solo_start=["셀마 라겔뢰프", "없는 문서"])
    monkeypatch.setattr(graph, "_invoke", fake)
    out = baseline.solo("여성 수상자", budget=6000, n_sections=3)
    spent = sum(v[2] for v in out["visited"])
    assert 6000 - graph.MIN_READ < spent <= 6000
    assert out["visited"][0][1] == "셀마 라겔뢰프"          # 카드 보고 고른 목록부터
    assert out["강제읽기"] >= 1                              # 멈추려 할 때 후보를 대신 읽혔다
    m, _ = metrics.measure(graph.record_of(out), graph.CORPUS)
    assert m["예산소진율"] > 95


def test_solo_is_measured_like_the_team(monkeypatch):
    monkeypatch.setattr(graph, "_invoke", FakeLLM(solo_start=["셀마 라겔뢰프", "그라치아 델레다"]))
    out = baseline.solo("여성 수상자", budget=3000, n_sections=2)
    assert len(out["sections"]) == 2                          # 짝 팀과 같은 절 수
    assert {c["누가"] for c in out["cost"]} == {"코디"}        # 한 사람이 전부 본다
    m, _ = metrics.measure(graph.record_of(out), graph.CORPUS)
    assert m["격리율"] == 100.0
    # 읽지 않은 문서 인용은 팀과 같은 규칙으로 지워지고 경보로 남는다
    assert "토니 모리슨" not in out["report"]
    assert m["제거된인용"] == 2 and m["허위인용"] == 0


def test_pair_run_picks_latest_default_team_run(tmp_path):
    import json
    rows = [{"id": "a", "질문id": "Q5", "종류": "팀", "설정": "기본"},
            {"id": "b", "질문id": "Q5", "종류": "팀", "설정": "끔-구역"},
            {"id": "c", "질문id": "Q5", "종류": "팀", "설정": "기본"},
            {"id": "d", "질문id": "Q2", "종류": "팀", "설정": "기본"}]
    path = tmp_path / "runs.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    assert baseline.pair_run("Q5", path=path)["id"] == "c"
    assert baseline.pair_run("Q5", "b", path=path)["id"] == "b"


def test_closest_prefers_question_words():
    pool = ["가브리엘 가르시아 마르케스", "셀마 라겔뢰프", "귄터 그라스"]
    # 셀마 라겔뢰프 문서 앞부분에는 '여성'·'노벨'·'수상자' 같은 단어가 나온다
    assert baseline.closest("여성 노벨 문학상 수상자", pool) == "셀마 라겔뢰프"


def test_broken_solo_report_is_rewritten_once(monkeypatch):
    fake = FakeLLM(solo_start=["셀마 라겔뢰프"])
    real = fake.__call__
    state = {"n": 0}

    def flaky(messages):
        if "혼자 정리하는 필자" in messages[0]["content"] and state["n"] == 0:
            state["n"] += 1
            return '{"제목": "깨진 JSON", "절": [{"제목": "a", "문장": [{"글": "끝나지 않은'
        return real(messages)

    monkeypatch.setattr(graph, "_invoke", flaky)
    out = baseline.solo("여성 수상자", budget=3000, n_sections=2)
    assert len(out["sections"]) == 2
    assert [c["단계"] for c in out["cost"]].count("집필(재)") == 1
