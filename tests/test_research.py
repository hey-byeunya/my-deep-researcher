"""서브에이전트 · 점검 — 가짜 모델로 네트워크 없이."""
import graph
from fakes import FakeLLM

SW = dict(graph.SWITCHES)


def task(**kw):
    base = {"절": "초기 여성 수상자", "번호": 0, "바퀴": 1, "역할": "생애 담당", "지시": "초기 여성 수상자를 정리",
            "시작문서": "셀마 라겔뢰프", "담당문서": ["셀마 라겔뢰프", "그라치아 델레다"],
            "예산": 5000, "피하기": []}
    return {**base, **kw}


# ─── 읽기 · 예산 ─────────────────────────────────────────

def test_reads_start_then_territory_within_budget(monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(graph, "_invoke", fake)
    out = graph.researcher({"task": task(), "prior": {}})
    sec = out["sections"][0]
    assert sec["읽은문서"][:2] == ["셀마 라겔뢰프", "그라치아 델레다"]   # 시작 → 담당 순서, 모델 호출 없이
    assert sec["이번_읽은글자"] <= 5000
    assert sum(v[2] for v in out["visited"]) == sec["이번_읽은글자"]
    # 비용 기록: 서브에이전트가 읽은 글자는 '서브' 로만 잡힌다
    assert {c["누가"] for c in out["cost"]} == {"서브"}


def test_long_doc_is_read_in_chunks_and_continued(monkeypatch):
    monkeypatch.setattr(graph, "_invoke", FakeLLM())
    long_doc = "윈스턴 처칠"                     # 약 5만 자
    t = task(시작문서=long_doc, 담당문서=[long_doc], 예산=5000)
    sec = graph.researcher({"task": t, "prior": {}})["sections"][0]
    cap = graph.CONFIG["문서당_읽기상한"]
    assert sec["읽은위치"][long_doc] == 5000               # 2,500자씩 두 번 — 이어 읽었다
    assert sec["이번_읽은글자"] == 5000
    # 다음 바퀴는 멈춘 곳에서 이어 읽는다
    sec2 = graph.researcher({"task": {**t, "바퀴": 2}, "prior": sec})["sections"][0]
    assert sec2["읽은위치"][long_doc] == 10000
    assert cap == 2500


def test_candidates_never_include_others_zone():
    read_pos = {"알베르 카뮈": 100}
    cand, _ = graph.candidates(read_pos, avoid={"장폴 사르트르"})
    assert "장폴 사르트르" not in cand
    assert "이방인 (소설)" in cand                          # 카뮈가 가리키는 작품
    # 링크가 바닥나도 구역은 풀지 않는다
    everything_but = set(graph.DOCS) - {"셀마 라겔뢰프"}
    cand, _ = graph.candidates({}, avoid=everything_but)
    assert cand == ["셀마 라겔뢰프"]


def test_fallback_candidates_are_laureates_by_year():
    cand, _ = graph.candidates({}, avoid=set())
    laureates = [c for c in cand if graph.KINDS.get(c) == "수상자"]
    assert laureates[0] == "쉴리 프뤼돔" and cand.index("쉴리 프뤼돔") == 0


def test_model_pick_outside_candidates_is_ignored(monkeypatch):
    fake = FakeLLM(picks=["장폴 사르트르"])               # 남의 구역을 고르려 한다
    monkeypatch.setattr(graph, "_invoke", fake)
    t = task(시작문서="", 담당문서=[], 피하기=["장폴 사르트르"])
    sec = graph.researcher({"task": t, "prior": {}})["sections"][0]
    assert "장폴 사르트르" not in sec["읽은문서"]


# ─── 인용 검사 ───────────────────────────────────────────

def test_check_citations():
    text = "라겔뢰프는 여성 최초다 «셀마 라겔뢰프». 한강은 2024년 «한강». 모리슨도 «토니 모리슨, 없는 책»."
    r = graph.check_citations(text, {"셀마 라겔뢰프", "한강 (작가)"})
    assert r["인용"] == ["셀마 라겔뢰프", "한강 (작가)", "토니 모리슨"]      # «한강» → 정식 제목
    assert r["허위인용"] == ["토니 모리슨"]                                # 읽지 않은 문서
    assert r["없는문서인용"] == ["없는 책"]


# ─── 두 번째 원고 ────────────────────────────────────────

def draft(cites, fake=(), missing=()):
    return {"인용": list(cites), "허위인용": list(fake), "없는문서인용": list(missing)}


def test_keep_new_rules():
    old = draft(["A", "B"])
    assert graph.keep_new(draft(["A", "B", "C"]), old)[0]
    assert not graph.keep_new(draft(["A"]), old)[0]                         # 근거가 줄었다
    assert not graph.keep_new(draft(["A", "B", "C"], fake=["C"]), old)[0]   # 허위인용
    assert not graph.keep_new(draft(["A", "B"], missing=["X"]), old)[0]     # 없는 문서


# ─── 점검 → 재위임 → 종합까지 ─────────────────────────────

PLAN = {"제목": "t", "목차": [
    {"절": "초기", "지시": "초기", "역할": "생애 담당", "시작문서": "셀마 라겔뢰프",
     "담당문서": ["셀마 라겔뢰프", "그라치아 델레다"], "비중": 2},
    {"절": "최근", "지시": "최근", "역할": "작품 담당", "시작문서": "한강 (작가)",
     "담당문서": ["한강 (작가)", "채식주의자 (소설)"], "비중": 2}]}


def test_only_self_reported_gaps_are_redispatched(monkeypatch):
    weak = {"본문": "짧다 «셀마 라겔뢰프». " * 10, "충분": False, "부족": "1920년대 수상자가 없다"}
    fake = FakeLLM(plan=PLAN, write={"초기": [weak]})
    monkeypatch.setattr(graph, "_invoke", fake)
    out = graph.run("여성 수상자")
    rounds = sorted((s["절"], s["바퀴"]) for s in out["sections"])
    assert rounds == [("초기", 1), ("초기", 2), ("최근", 1)]              # 부족 신고한 '초기'만 2바퀴
    assert any("재위임" in l for l in out["log"])
    # 2바퀴 원고는 첫 원고의 읽은 자리에서 이어 읽고, 재위임 지시를 받았다
    second = next(s for s in out["sections"] if s["바퀴"] == 2)
    assert "재위임" in second["지시"] and "1920년대" in second["지시"]


def test_worse_second_draft_is_rejected(monkeypatch):
    first = {"본문": "a «셀마 라겔뢰프» b «그라치아 델레다». " * 8, "충분": False, "부족": "더 필요"}
    worse = {"본문": "인용을 빠뜨린 원고. " * 20, "충분": True, "부족": ""}
    fake = FakeLLM(plan=PLAN, write={"초기": [first, worse]})
    monkeypatch.setattr(graph, "_invoke", fake)
    out = graph.run("여성 수상자")
    assert out["plan"]["채택"]["초기"] == 1                              # 첫 원고를 지킨다
    assert any("버림" in l for l in out["log"])


def test_switch_off_redelegation(monkeypatch):
    weak = {"본문": "짧다 «셀마 라겔뢰프». " * 10, "충분": False, "부족": "x"}
    monkeypatch.setattr(graph, "_invoke", FakeLLM(plan=PLAN, write={"초기": [weak]}))
    out = graph.run("여성 수상자", 재위임=False)
    assert {s["바퀴"] for s in out["sections"]} == {1}


def test_sections_run_isolated(monkeypatch):
    """서브에이전트는 남의 담당문서를 읽지 않고, 코디네이터 비용과 섞이지 않는다."""
    fake = FakeLLM(plan=PLAN)
    monkeypatch.setattr(graph, "_invoke", fake)
    out = graph.run("여성 수상자", 재위임=False)
    by = {s["절"]: set(s["읽은문서"]) for s in out["sections"]}
    assert not ({"한강 (작가)", "채식주의자 (소설)"} & by["초기"])
    assert not ({"셀마 라겔뢰프", "그라치아 델레다"} & by["최근"])
    coord = sum(c["글자"] for c in out["cost"] if c["누가"] == "코디")
    sub = sum(c["글자"] for c in out["cost"] if c["누가"] == "서브")
    assert coord > 0 and sub > 0


def test_each_assigned_doc_is_sampled_before_continuing(monkeypatch):
    """긴 담당문서 하나를 끝까지 파느라 나머지를 못 읽는 일이 없어야 한다."""
    monkeypatch.setattr(graph, "_invoke", FakeLLM())
    t = task(시작문서="윈스턴 처칠", 담당문서=["윈스턴 처칠", "셀마 라겔뢰프"], 예산=5000)
    sec = graph.researcher({"task": t, "prior": {}})["sections"][0]
    assert sec["읽은문서"][:2] == ["윈스턴 처칠", "셀마 라겔뢰프"]


def test_writer_json_with_short_body_is_kept_as_is(monkeypatch):
    fake = FakeLLM(write={"초기 여성 수상자": {"본문": "자료가 없다.", "충분": False, "부족": "자료 없음"}})
    monkeypatch.setattr(graph, "_invoke", fake)
    sec = graph.researcher({"task": task(), "prior": {}})["sections"][0]
    assert sec["본문"] == "자료가 없다." and sec["부족"] == "자료 없음"


def test_empty_territory_sections_are_not_dispatched(monkeypatch):
    plan = {"목차": [
        {"절": "초기", "지시": "x", "역할": "생애 담당", "시작문서": "셀마 라겔뢰프", "담당문서": [], "비중": 2},
        {"절": "비교", "지시": "x", "역할": "비교 담당", "시작문서": "셀마 라겔뢰프", "담당문서": [], "비중": 2}]}
    monkeypatch.setattr(graph, "_invoke", FakeLLM(plan=plan))
    out = graph.run("여성 수상자", plan_only=True)
    toc = out["plan"]["목차"]
    assert [t["절"] for t in toc] == ["초기"]
    assert toc[0]["예산"] >= graph.CONFIG["절수"] * graph.CONFIG["절예산_글자"] * 0.99 / 1   # 전체 예산 유지
    assert any("구역이 빈 절" in f for f in out["plan"]["교정"])


def test_citation_less_draft_is_rewritten_once(monkeypatch):
    bare = {"본문": "라겔뢰프는 여성 최초 수상자다. " * 10, "충분": True, "부족": ""}
    cited = {"본문": "라겔뢰프는 여성 최초 수상자다 «셀마 라겔뢰프». " * 10, "충분": True, "부족": ""}
    fake = FakeLLM(write={"초기 여성 수상자": [bare, cited]})
    monkeypatch.setattr(graph, "_invoke", fake)
    out = graph.researcher({"task": task(), "prior": {}})
    sec = out["sections"][0]
    assert sec["인용보강"] and sec["인용"] == ["셀마 라겔뢰프"] * 10
    assert [c["단계"] for c in out["cost"]].count("집필(재)") == 1      # 한 번만, 읽기 없이
