"""check_seeds.compare 가 세 갈래로 제대로 나누는지 (네트워크 없이)."""
from check_seeds import _norm, compare, pair_by_name


def test_compare_splits_three_ways():
    official = [
        {"id": "1", "이름": "Han Kang", "연도": ["2024"], "wikidata": "Q1"},
        {"id": "2", "이름": "New Laureate", "연도": ["2025"], "wikidata": "Q2"},
        {"id": "3", "이름": "No Korean Page", "연도": ["1904"], "wikidata": "Q3"},
    ]
    ko = {"Q1": "한강 (작가)", "Q2": "새 수상자"}
    seeds = ["한강 (작가)", "잘못 붙은 문서"]
    r = compare(official, ko, seeds)
    assert [x["kowiki"] for x in r["일치"]] == ["한강 (작가)"]
    assert [x["kowiki"] for x in r["추가_후보"]] == ["새 수상자"]
    assert [x["이름"] for x in r["한국어_문서_없음"]] == ["No Korean Page"]
    assert r["공식_명단에_없음"] == ["잘못 붙은 문서"]


def test_norm_strips_accents():
    assert _norm("Le Clézio") == "le clezio"


def test_pair_by_name_rescues_wrong_official_qid():
    """공식 API 의 르 클레지오 위키데이터 ID 는 그의 작품 항목을 가리킨다 (2026-09-23 확인)."""
    no_ko = [{"이름": "Jean-Marie Gustave Le Clézio", "성": "Le Clézio", "연도": ["2008"], "wikidata": "Q7884119"}]
    extra = ["장마리 귀스타브 르 클레지오", "엉뚱한 문서"]
    english = {"장마리 귀스타브 르 클레지오": ["J. M. G. Le Clézio", "J. M. G. Le Clézio"],
               "엉뚱한 문서": ["Something Else", ""]}
    pairs, left_official, left_seeds = pair_by_name(no_ko, extra, english)
    assert [p["kowiki"] for p in pairs] == ["장마리 귀스타브 르 클레지오"]
    assert left_official == [] and left_seeds == ["엉뚱한 문서"]
