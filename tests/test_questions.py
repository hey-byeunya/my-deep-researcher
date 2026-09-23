"""질문 세트가 강의 요구(8건 이상 · 성격 섞기 · 한 건형 포함 · 정답표 없음 · 이유 한 줄)를 지키는지."""
import json
from collections import Counter
from pathlib import Path

Q = json.loads((Path(__file__).parent.parent / "data" / "questions.json").read_text(encoding="utf-8"))
QS = Q["questions"]


def test_at_least_8_questions():
    assert len(QS) >= 8


def test_ids_unique():
    assert len({q["id"] for q in QS}) == len(QS)


def test_types_are_mixed_and_include_single_source():
    kinds = Counter(q["성격"] for q in QS)
    assert set(kinds) == {"한건", "흩어진", "추적"}
    assert kinds["한건"] >= 1          # 이 구조를 쓰면 안 되는 경우를 일부러 넣는다


def test_every_question_has_a_reason_and_no_answer_key():
    for q in QS:
        assert q["질문"].strip() and q["왜_나눌_만한가"].strip(), q["id"]
        assert not ({"정답", "answer", "기대문서", "정답문서"} & set(q)), q["id"]   # 정답표 금지
