"""문서 제목 맞추기 — graph(인용 검사 · 배정 검사)와 metrics(근거율)가 같은 규칙을 쓰도록 한 곳에 둔다.

절 제목의 기간(시대 절) 규칙도 여기 둔다 — graph 의 배정 검사와 metrics 의 '시대불일치' 경보가 같은 규칙을 쓴다.

규칙이 두 곳에 따로 있으면 같은 «…» 표시를 한쪽은 인정하고 다른 쪽은 안 세는 일이 생긴다
(2026-09-23 코드 리뷰 High 2 — 실제로 metrics 가 «A, B» 를 나누지 않아 그 문장을 근거 없음으로 셌다).
"""
import re

# 카드에 붙인 수상 연도를 제목째 베껴 오는 경우 ("셀마 라겔뢰프 (1909년 수상)", "한강 (작가) ｜ 2024년 수상")
_YEAR_TAIL = re.compile(r"\s*[(｜|]\s*\d{4}(·\d{4})*년 수상.*$")
_QUOTES = "«»《》『』「」\"'"


def resolve_title(name, docs):
    """모델이 적은 이름을 코퍼스 제목으로 맞춘다. 못 맞추면 None.

    '한강' -> '한강 (작가)'(괄호 구분자가 빠진 경우), 따옴표 · 인용 괄호, 베껴 온 수상 연도 꼬리만 구제한다.
    """
    name = (name or "").strip().strip(_QUOTES)
    name = _YEAR_TAIL.sub("", name).strip()
    if name in docs:
        return name
    base = {re.sub(r"\s*\([^)]*\)$", "", t): t for t in docs}
    return base.get(name)


def split_refs(inside):
    """«A, B» · «A·B» 처럼 한 괄호에 묶인 근거를 나눈다. 코퍼스 제목에는 쉼표 · 가운뎃점이 없다."""
    return [p for p in re.split(r"\s*[,·]\s*", inside) if p.strip()]


# ─── 시대 절 ─────────────────────────────────────────────────────
_Y = r"(1[89]\d\d|20\d\d)(년대|년)?"
_ERA_SPAN = re.compile(_Y + r"(?:\s*[-–~]|[^\d()]{0,12}?부터)\s*" + _Y)   # 1951-2000 · 1909년 라겔뢰프부터 1945년까지
_ERA_AFTER = re.compile(_Y + r"\s*(?:이후|부터|~\s*$|[-–]\s*$|[-–]\s*현재)")   # 2000년 이후 · 2001~
_ERA_DECADE = re.compile(r"(1[89]\d0|20\d0)년대")                    # 1980년대


def _end(year, unit):
    """'1970년대까지'는 1979년까지다."""
    return int(year) + 9 if unit == "년대" else int(year)


def era_range(section_title):
    """절 제목의 기간 — 없으면 None.

      '중기 수상 (1951-2000)' -> (1951, 2000) · '1909년부터 1970년대까지' -> (1909, 1979)
      '2000년 이후의 여성 수상자' -> (2000, 9999) · '1980년대 수상자' -> (1980, 1989)
    처음엔 '1951-2000' 꼴만 읽어서, 모델이 「1945년부터 2000년까지」라고 쓰면 옐리네크(2004)가 그대로 남았다.
    """
    title = section_title or ""
    m = _ERA_SPAN.search(title)
    if m:
        return int(m[1]), _end(m[3], m[4])
    m = _ERA_AFTER.search(title)
    if m:
        return int(m[1]), 9999
    m = _ERA_DECADE.search(title)
    if m:
        return int(m[1]), int(m[1]) + 9
    return None


def era_mismatches(toc, years):
    """시대 절에 기간 밖에 상을 받은 수상자가 배정된 곳 [(절, 문서, 수상 연도)]. 수상자 문서만 본다."""
    bad = []
    for t in toc:
        span = era_range(t.get("절", ""))
        if not span:
            continue
        for d in t.get("담당문서", []):
            if d in years and not span[0] <= int(years[d][0]) <= span[1]:
                bad.append((t["절"], d, int(years[d][0])))
    return bad
