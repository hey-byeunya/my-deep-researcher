"""지표 — 정답표도 판정 모델도 쓰지 않는다. 만들어진 글과 실제로 읽은 자료만 본다.

measure(run, corpus) 는 저장된 실행 기록 하나만 있으면 계산된다. 팀(graph.py)·혼자 하는 대조군
(baseline.py)·데모(app.py)가 모두 이 함수를 쓰고, LLM 을 부르지 않는다.

지표는 두 종류다.
  · 신호 — 설정끼리 높낮이를 견준다. 순위표로 쓰지 않고, 명백한 실패를 거르는 데만 쓴다.
  · 경보 — 0 이어야 한다. 0 이 아니면 어느 장치가 고장 났는지 바로 가리킨다.
지표마다 '어느 장치를 보는 신호인지'를 METRICS 에 적어 둔다. 한 줄로 설명이 안 되는 지표는 넣지 않았다.
"""
import re
from collections import Counter

from titles import resolve_title, split_refs

# 이름: (종류, 보는 장치, 한 줄 설명)
METRICS = {
    # ── 신호 ──
    "근거율":        ("신호", "집필",   "보고서 문장 중 읽은 문서를 «»로 댄 문장의 비율(%)"),
    "예산소진율":    ("신호", "예산",   "받은 읽기 예산(글자) 중 실제로 읽은 비율(%) — 낮으면 일찍 멈췄다"),
    "중복률":        ("신호", "구역",   "절끼리 겹쳐 읽은 비율(%) — 공용 서가는 빼고 센다"),
    "인용편중":      ("신호", "배정·탐색", "가장 많이 인용된 문서 하나가 전체 인용에서 차지하는 비율(%)"),
    "읽고안쓴비율":  ("신호", "탐색",   "읽었지만 보고서에 한 번도 인용하지 않은 문서의 비율(%)"),
    "격리율":        ("신호", "격리",   "모델이 본 글자 중 코디네이터 몫(%) — 수업 정의, 예산이 작으면 커 보인다"),
    "코디카드열람률": ("신호", "격리",  "코디네이터가 기획 한 번에 본 글자 ÷ 코퍼스 전체 글자(%) — 원문은 보지 않는다"),
    "읽은문서":      ("신호", "탐색",   "한 번이라도 읽은 서로 다른 문서 수"),
    "보고서자수":    ("신호", "종합",   "최종 보고서 글자 수"),
    "LLM호출":       ("신호", "비용",   "모델을 부른 횟수"),
    # ── 경보 (0 이어야 한다) ──
    "허위인용":      ("경보", "집필·인용검사", "보고서에 인용됐지만 그 절이 읽지 않은 문서 수"),
    "없는문서인용":  ("경보", "집필",   "«» 안에 코퍼스에 없는 이름을 넣은 수"),
    "인용0곳절":     ("경보", "집필",   "근거 표시가 하나도 없는 절 수"),
    "제거된인용":    ("경보", "집필·인용검사", "인용 보강 재집필에서 코드가 지운 틀린 «» 표시 수"),
    "빈원고절":      ("경보", "탐색·집필", "원고가 100자 미만인 절 수"),
    "절간중복문서":  ("경보", "구역",   "공용 서가가 아닌데 두 절 이상이 읽은 문서 수"),
    "구역겹침":      ("경보", "기획",   "코디네이터가 두 절에 같은 문서를 배정하려 한 건수(코드가 막았다)"),
    "배정실패":      ("경보", "기획",   "코디네이터가 코퍼스에 없는 제목을 배정한 건수(코드가 막았다)"),
    "재기획":        ("경보", "기획",   "목차 문제로 코디네이터에게 다시 짜게 한 횟수"),
}


def sentences(text):
    """문장 나누기 규칙 — 바꾸면 근거율이 달라진다. 규칙을 고칠 때는 tests/test_metrics.py 를 같이 고친다.

    마침표·물음표·느낌표 뒤의 공백, 또는 줄바꿈에서 자른다. 제목(#)과 15자 이하 조각은 문장으로 치지 않는다.
    «» 근거 표시가 문장 부호 뒤에 붙는 경우("…했다. «셀마 라겔뢰프»")가 흔해서, 부호 뒤에 바로 오는
    «…» 는 앞 문장에 붙여 준다.
    """
    text = re.sub(r"([.!?])\s*(«[^»]+»)", r"\2\1", text)          # "했다. «A»" → "했다«A»."
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip() for p in parts if len(p.strip()) > 15 and not p.strip().startswith("#")]


def citations(text):
    return re.findall(r"«([^»]+)»", text)


def measure(run, corpus):
    """run: {"report", "sections"(채택 원고만), "visited", "cost", "plan"}. corpus: {"docs", "_종류"}."""
    docs = corpus["docs"]
    shared = {t for t, k in corpus.get("_종류", {}).items() if k == "공통"}
    total_corpus = sum(map(len, docs.values()))
    report = run.get("report", "")
    sections = run.get("sections", [])
    visited = run.get("visited", [])
    cost = run.get("cost", [])
    plan = run.get("plan", {})

    # ── 근거율: 문장마다 «» 인용이 있고 그 문서가 코퍼스에 있으며 누군가 읽었는가
    read_any = {v[1] for v in visited}
    sents = sentences(report)
    def refs(text):   # «A, B» 는 A 와 B 두 건 — graph.check_citations 와 같은 규칙(titles.py)
        return [t for c in citations(text) for t in (resolve_title(p, docs) for p in split_refs(c)) if t]

    grounded = [s for s in sents if any(t in read_any for t in refs(s))]
    cited_all = refs(report)
    use = Counter(cited_all)

    # ── 절 단위: 채택 원고의 절이 읽은 문서로만 인용했는가
    fake = sum(len(s.get("허위인용", [])) for s in sections)
    missing = sum(len(s.get("없는문서인용", [])) for s in sections)

    # ── 구역: 절마다 읽은 문서 집합 (바퀴를 합쳐서)
    by_section = {}
    for v in visited:
        by_section.setdefault(v[0], set()).add(v[1])
    pairs = [(sec, d) for sec, ds in by_section.items() for d in ds if d not in shared]
    owners = Counter(d for _, d in pairs)
    overlap_docs = sorted(d for d, n in owners.items() if n >= 2)

    # ── 예산: 파견될 때마다 받은 예산 합 대비 실제로 읽은 글자
    given = sum(s.get("예산", 0) for s in run.get("all_drafts", sections))
    spent = sum(v[2] for v in visited)

    coord = sum(c["글자"] for c in cost if c["누가"] == "코디")
    sub = sum(c["글자"] for c in cost if c["누가"] == "서브")
    plan_calls = [c["글자"] for c in cost if c["누가"] == "코디" and c.get("단계", "").startswith("기획")]
    fixes = plan.get("교정", [])

    m = {
        "근거율": round(100 * len(grounded) / max(len(sents), 1), 1),
        "예산소진율": round(100 * spent / given, 1) if given else 0.0,
        "중복률": round(100 * (1 - len(owners) / len(pairs)), 1) if pairs else 0.0,
        "인용편중": round(100 * max(use.values()) / sum(use.values()), 1) if use else 0.0,
        "읽고안쓴비율": round(100 * len(read_any - set(use)) / max(len(read_any), 1), 1),
        "격리율": round(100 * coord / max(coord + sub, 1), 1),
        "코디카드열람률": round(100 * max(plan_calls, default=0) / max(total_corpus, 1), 1),
        "읽은문서": len(read_any),
        "보고서자수": len(report),
        "LLM호출": len(cost),
        "허위인용": fake,
        "없는문서인용": missing,
        "인용0곳절": sum(1 for s in sections if not s.get("인용")),
        "제거된인용": sum(len(s.get("제거된인용", [])) for s in sections),
        "빈원고절": sum(1 for s in sections if len(s.get("본문", "")) < 100),
        "절간중복문서": len(overlap_docs),
        "구역겹침": sum("구역 겹침" in f for f in fixes),
        "배정실패": sum("코퍼스에 없어" in f for f in fixes),
        "재기획": sum(c.get("단계") == "기획(재)" for c in cost),
    }
    detail = {"문장수": len(sents), "근거붙은문장": len(grounded), "인용수": len(cited_all),
              "절간중복문서": overlap_docs, "읽고안쓴": sorted(read_any - set(use)),
              "최다인용": use.most_common(3), "코디글자": coord, "서브글자": sub,
              "읽은글자": spent, "받은예산": given}
    return m, detail


def alarms(m):
    """0 이 아닌 경보만."""
    return {k: v for k, v in m.items() if METRICS.get(k, ("",))[0] == "경보" and v}


def one_line(m):
    """로그용 한 줄 요약."""
    a = alarms(m)
    alarm = " · ".join(f"{k} {v}" for k, v in a.items()) or "없음"
    return (f"근거율 {m['근거율']}% · 예산소진 {m['예산소진율']}% · 중복 {m['중복률']}% · "
            f"편중 {m['인용편중']}% · 격리율 {m['격리율']}%(카드 {m['코디카드열람률']}%) · "
            f"LLM {m['LLM호출']}회 · 경보: {alarm}")
