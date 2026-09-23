"""collect_corpus 의 판정 규칙(네트워크 없이)과, 저장된 corpus.json 의 불변식을 검사한다."""
import json
import statistics
from pathlib import Path

import pytest

import collect_corpus as cc

CORPUS = Path(__file__).parent.parent / "data" / "corpus.json"


# ─── 판정 규칙 ────────────────────────────────────────────

def test_base_name_strips_disambiguation():
    assert cc.base_name("페스트 (소설)") == "페스트"
    assert cc.base_name("한강 (작가)") == "한강"
    assert cc.base_name("설국") == "설국"


def test_excluded_titles():
    for t in ["1957년", "20세기", "노벨 문학상 수상자 목록", "틀:노벨 문학상", "분류:소설"]:
        assert cc.is_excluded(t), t
    assert not cc.is_excluded("스웨덴 한림원")


def test_mentioned_requires_body_occurrence():
    text = "카뮈는 1942년 《이방인》을 발표했다."
    assert cc.mentioned("이방인 (소설)", text)
    assert not cc.mentioned("페스트 (소설)", text)
    assert not cc.mentioned("시 (문학)", "시를 썼다")      # 한 글자 이름은 우연히 겹친다


def test_is_work_rejects_people():
    assert cc.is_work(["1942년 소설", "프랑스의 소설"])
    assert cc.is_work(["파블로 네루다의 시집"])
    assert not cc.is_work(["프랑스의 소설가", "1913년 출생"])      # 작가 문서
    assert not cc.is_work(["독일의 철학자"])                         # 작품 표지 없음


def test_name_tokens_and_author_mention():
    assert cc.name_tokens("알베르 카뮈") == ["알베르", "카뮈"]
    assert cc.name_tokens("T. S. 엘리엇") == ["엘리엇"]
    assert cc.mentions_author("알베르 카뮈", "《페스트》는 카뮈의 장편 소설이다")
    assert not cc.mentions_author("알베르 카뮈", "《자본론》은 마르크스의 저작이다")   # 남의 책


def test_owns_work_uses_author_category():
    """첫 수집에서 잘못 붙었던 사례들 (2026-09-23)."""
    assert cc.owns_work("헤르만 헤세", ["1919년 소설", "헤르만 헤세의 소설"], "《데미안》은 ...")
    assert cc.owns_work("버트런드 러셀", ["1910년 책", "버트런드 러셀의 작품"], "《수학 원리》는 ...")
    # 남의 작품 — 본문에 작가 이름이 나와도 분류가 다른 사람을 가리킨다
    assert not cc.owns_work("오에 겐자부로", ["서사시", "단테 알리기에리의 작품"], "오에 겐자부로는 신곡을 ...")
    assert not cc.owns_work("버트런드 러셀", ["루트비히 비트겐슈타인의 작품"], "러셀이 서문을 썼다")


def test_owns_work_without_author_category_needs_lead():
    # 나라 이름 분류는 작가 판정에 쓰지 않는다
    britannica = "브리태니커 백과사전은 1768년부터 발행된 영어 백과사전이다." + "…" * 300 + "아나톨 프랑스"
    assert not cc.owns_work("아나톨 프랑스", ["1768년 책", "미국의 책"], britannica)
    assert cc.owns_work("한강 (작가)", ["2007년 소설"], "《채식주의자》는 한강의 연작 소설이다.")


def test_body_links_keeps_only_pool_and_body():
    raw = ["이방인 (소설)", "페스트 (소설)", "이방인 (소설)", "파리"]
    pool = {"이방인 (소설)", "페스트 (소설)"}
    assert cc.body_links(raw, "《이방인》을 썼다. 파리에 살았다.", pool) == ["이방인 (소설)"]


# ─── 저장된 코퍼스의 불변식 ────────────────────────────────

@pytest.fixture(scope="module")
def corpus():
    if not CORPUS.exists():
        pytest.skip("data/corpus.json 이 아직 없다 — python collect_corpus.py")
    return json.loads(CORPUS.read_text(encoding="utf-8"))


def test_at_least_30_docs(corpus):
    assert len(corpus["docs"]) >= 30


def test_all_seeds_present(corpus):
    """수상자는 길이와 상관없이 전원 들어가야 한다."""
    assert set(cc.SEEDS) <= set(corpus["docs"])


def test_no_stubs_except_seeds(corpus):
    short = [t for t, b in corpus["docs"].items()
             if len(b) < cc.MIN_CHARS and corpus["_종류"][t] != "수상자"]
    assert not short


def test_links_point_inside_corpus(corpus):
    docs = corpus["docs"]
    for t, ls in corpus["links"].items():
        assert t in docs
        assert t not in ls, f"{t} 가 자기 자신을 가리킨다"
        assert all(l in docs for l in ls), t


def test_works_per_author_cap(corpus):
    per = {}
    for work, author in corpus["_작가"].items():
        assert corpus["_종류"][work] == "작품"
        assert author in corpus["docs"]
        per[author] = per.get(author, 0) + 1
    assert max(per.values(), default=0) <= cc.WORKS_PER_AUTHOR


def test_not_a_complete_graph(corpus):
    """navbox 를 걸러 냈다면 수상자가 수상자 전원을 가리키는 일은 없어야 한다."""
    seeds = [t for t, k in corpus["_종류"].items() if k == "수상자"]
    seed_links = [sum(l in seeds for l in corpus["links"][s]) for s in seeds]
    assert statistics.median(seed_links) < len(seeds) * 0.2
