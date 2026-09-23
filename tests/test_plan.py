"""기획·배치 — 가짜 LLM 으로 네트워크 없이 검사한다."""
import json

import pytest

import graph

SW = dict(graph.SWITCHES)


def fake_llm(reply):
    def _invoke(messages):
        return reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
    return _invoke


# ─── 예산 나누기 ─────────────────────────────────────────

def test_budget_keeps_total_and_floor():
    b = graph.allocate_budget([3, 1, 1, 1], 80000, 0.5)
    assert abs(sum(b) - 80000) <= len(b)          # 정수 내림 오차만
    assert min(b) >= 80000 / 4 * 0.5 * 0.99        # 가장 작은 절도 평균의 절반은 받는다
    assert b[0] > b[1]


def test_budget_bad_weights_fall_back_to_equal():
    b = graph.allocate_budget(["많이", None, 2], 60000, 0.5)
    assert b[0] == b[1] == b[2]


# ─── 목차 검사 ───────────────────────────────────────────

def plan_obj(*items):
    return {"제목": "t", "목차": [{"절": f"절{i}", "지시": "x", "역할": "작품 담당", "비중": 2, **it}
                                 for i, it in enumerate(items)]}


def test_resolve_title_strips_copied_award_year():
    """모델이 카드 줄을 통째로 베껴 오면 연도 꼬리를 떼고 맞춘다 (실제로 겪은 일)."""
    assert graph.resolve_title("셀마 라겔뢰프 (1909년 수상)", graph.DOCS) == "셀마 라겔뢰프"
    assert graph.resolve_title("한강 (작가) ｜ 2024년 수상", graph.DOCS) == "한강 (작가)"
    assert graph.resolve_title("여성 수상자 목록", graph.DOCS) is None


def test_validate_plan_fixes_bad_assignments():
    obj = plan_obj({"시작문서": "한강"},                 # 괄호 없는 이름 → '한강 (작가)'
                   {"시작문서": "존재하지 않는 책"},        # 코퍼스에 없음 → 비움
                   {"시작문서": "한강 (작가)"},          # 앞 절과 겹침 → 비움
                   {"시작문서": "데미안", "역할": "탐정"})  # 명단에 없는 역할 → 기본역할
    toc, fixes = graph.validate_plan(obj, graph.DOCS, graph.ROSTER, graph.CONFIG, SW)
    assert [t["시작문서"] for t in toc] == ["한강 (작가)", "", "", "데미안"]
    assert toc[3]["역할"] == graph.CONFIG["기본역할"]
    assert len(fixes) == 4
    assert all(t["시작문서"] == "" or t["시작문서"] in graph.DOCS for t in toc)


def test_territories_never_overlap():
    obj = plan_obj({"시작문서": "셀마 라겔뢰프", "담당문서": ["셀마 라겔뢰프", "그라치아 델레다", "펄 S. 벅"]},
                   {"시작문서": "펄 S. 벅", "담당문서": ["펄 S. 벅", "토니 모리슨", "없는 문서"]})
    toc, fixes = graph.validate_plan(obj, graph.DOCS, graph.ROSTER, graph.CONFIG, SW)
    assert toc[0]["담당문서"] == ["셀마 라겔뢰프", "그라치아 델레다", "펄 S. 벅"]
    assert toc[1]["담당문서"] == ["토니 모리슨"]           # 겹친 벅·없는 문서는 빠진다
    assert toc[1]["시작문서"] == "토니 모리슨"             # 시작문서가 빠지면 담당문서 첫 건으로
    assert any("구역 겹침" in f for f in fixes)
    assert not set(toc[0]["담당문서"]) & set(toc[1]["담당문서"])


def test_shared_docs_can_be_used_by_many_sections():
    """공용 서가(공통 문서)는 여러 절이 담당해도 겹침이 아니다."""
    obj = plan_obj({"시작문서": "노벨 문학상", "담당문서": ["셀마 라겔뢰프"]},
                   {"시작문서": "노벨 문학상", "담당문서": ["토니 모리슨"]})
    toc, fixes = graph.validate_plan(obj, graph.DOCS, graph.ROSTER, graph.CONFIG, SW)
    assert toc[0]["시작문서"] == toc[1]["시작문서"] == "노벨 문학상"
    assert not any("구역 겹침" in f for f in fixes)


def test_validate_plan_flags_format_sections():
    obj = {"목차": [{"절": "개요", "시작문서": "노벨 문학상", "역할": "수상 담당"},
                    {"절": "결론", "시작문서": "노벨상", "역할": "수상 담당"}]}
    _, fixes = graph.validate_plan(obj, graph.DOCS, graph.ROSTER, graph.CONFIG, SW)
    assert sum("형식 단위" in f for f in fixes) == 2


def test_validate_plan_caps_sections():
    obj = plan_obj(*[{"시작문서": t} for t in list(graph.DOCS)[:9]])
    toc, _ = graph.validate_plan(obj, graph.DOCS, graph.ROSTER, graph.CONFIG, SW)
    assert len(toc) == graph.CONFIG["절수"]


def test_switches_change_the_plan():
    """스위치를 끄면 실제로 값이 바뀌어야 한다 (로그만 찍히는 사고 방지)."""
    obj = plan_obj({"시작문서": "데미안"}, {"시작문서": "설국", "역할": "시대 담당"})
    toc, _ = graph.validate_plan(obj, graph.DOCS, graph.ROSTER, graph.CONFIG, {**SW, "배정": False})
    assert all(t["시작문서"] == "" for t in toc)
    toc, _ = graph.validate_plan(obj, graph.DOCS, graph.ROSTER, graph.CONFIG, {**SW, "역할": False})
    assert {t["역할"] for t in toc} == {graph.CONFIG["기본역할"]}


# ─── 카드 · 구역 · 배치 ──────────────────────────────────

def test_cards_hide_work_bodies():
    text = graph.cards()
    assert "· 작품 문서:" in text
    assert "- 데미안:" not in text                        # 작품은 제목만, 카드로 따로 두지 않는다
    assert len(text) < sum(map(len, graph.DOCS.values())) * 0.1   # 코디네이터는 코퍼스의 10% 미만만 본다


def test_cards_show_award_year_in_order():
    text = graph.cards()
    assert "- 셀마 라겔뢰프 ｜ 1909년 수상 ｜" in text
    assert "- 한강 (작가) ｜ 2024년 수상 ｜" in text
    assert "연도 미상" not in text                                   # 122명 모두 공식 연도가 있다
    assert text.index("쉴리 프뤼돔 ｜ 1901") < text.index("한강 (작가) ｜ 2024")  # 연도순
    assert text.index("[공용 서가") < text.index("[수상자")


def test_zones_respect_switch():
    toc = [{"절": "A", "시작문서": "데미안", "담당문서": ["데미안"]},
           {"절": "B", "시작문서": "설국", "담당문서": ["설국", "오에 겐자부로", "노벨 문학상"]}]
    done = {"B": {"읽은문서": ["설국", "가와바타 야스나리", "노벨상"]}}
    # 공용 서가(노벨 문학상 · 노벨상)는 남이 담당했거나 읽었어도 피하지 않는다
    assert graph.zones(toc, done, 0, SW) == ["가와바타 야스나리", "설국", "오에 겐자부로"]
    # 담당구역을 끄면 수업 방식 — 남의 시작문서 + 읽은 문서만
    assert graph.zones(toc, done, 0, {**SW, "담당구역": False}) == ["가와바타 야스나리", "설국"]
    assert graph.zones(toc, done, 0, {**SW, "구역": False}) == []


def test_fanout_sends_one_per_section():
    state = {"question": "q", "switches": SW, "sections": [],
             "plan": {"목차": [{"절": "A", "시작문서": "데미안", "역할": "작품 담당", "예산": 1},
                              {"절": "B", "시작문서": "설국", "역할": "작품 담당", "예산": 1}],
                      "배치": [0, 1], "바퀴": 1}}
    sends = graph.fanout(state)
    assert len(sends) == 2
    assert sends[0].arg["task"]["피하기"] == ["설국"]


# ─── 전 구간(빈 노드 포함)이 오류 없이 도는가 ─────────────────

def test_run_end_to_end_with_fake_llm(monkeypatch):
    reply = plan_obj({"시작문서": "셀마 라겔뢰프"}, {"시작문서": "한강 (작가)"})
    monkeypatch.setattr(graph, "_invoke", fake_llm(reply))
    out = graph.run("여성 수상자들의 흐름은?")
    assert len(out["plan"]["목차"]) == 2
    assert len(out["sections"]) == 2
    coord = [c for c in out["cost"] if c["누가"] == "코디"]
    assert coord and coord[0]["단계"] == "기획"


def test_broken_json_falls_back(monkeypatch):
    monkeypatch.setattr(graph, "_invoke", fake_llm("목차를 못 짰습니다"))
    out = graph.run("아무 질문", plan_only=True)
    assert len(out["plan"]["목차"]) == 1
    assert out["plan"]["교정"]


def test_overlapping_plan_is_replanned_once(monkeypatch):
    """구역이 겹치는 목차(형식 단위)는 한 번 다시 짜게 하고, 나아지면 그쪽을 쓴다."""
    bad = plan_obj({"시작문서": "셀마 라겔뢰프", "담당문서": ["그라치아 델레다", "펄 S. 벅"]},
                   {"시작문서": "셀마 라겔뢰프", "담당문서": ["그라치아 델레다", "펄 S. 벅"]})
    good = plan_obj({"시작문서": "셀마 라겔뢰프", "담당문서": ["그라치아 델레다"]},
                    {"시작문서": "토니 모리슨", "담당문서": ["앨리스 먼로"]})
    replies = iter([bad, good])
    monkeypatch.setattr(graph, "_invoke", lambda m: json.dumps(next(replies), ensure_ascii=False))
    out = graph.run("여성 수상자", plan_only=True)
    assert [c["단계"] for c in out["cost"]] == ["기획", "기획(재)"]
    assert out["plan"]["목차"][1]["담당문서"] == ["토니 모리슨", "앨리스 먼로"]
    assert out["plan"]["교정"][0].startswith("(재기획 채택)")


def test_clean_plan_is_not_replanned(monkeypatch):
    good = plan_obj({"시작문서": "셀마 라겔뢰프"}, {"시작문서": "토니 모리슨"})
    monkeypatch.setattr(graph, "_invoke", fake_llm(good))
    out = graph.run("여성 수상자", plan_only=True)
    assert [c["단계"] for c in out["cost"]] == ["기획"]
