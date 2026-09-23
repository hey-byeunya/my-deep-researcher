#!/usr/bin/env python3
"""1단계 — 한국어 위키백과에서 노벨 문학상 코퍼스를 모아 data/corpus.json 한 파일로 저장한다.

  python collect_corpus.py            # 코퍼스가 없으면 모으고, 있으면 통계만 출력한다
  python collect_corpus.py --refresh  # 있는 코퍼스를 버리고 처음부터 다시 모은다
  python collect_corpus.py --stats    # 모으지 않고 통계만 출력한다

강의의 「위키백과 코퍼스 수집 프롬프트」를 따르고, 아래 네 가지만 이 도메인에 맞게 바꿨다.
  ① 토막글 기준 3,000자 → 800자, 그리고 수상자는 길이와 상관없이 전원 넣는다.
     수상자 문서 대부분이 짧아(위키텍스트 중앙값 약 5.5KB) 3,000자로 자르면 121명 중 20~40명만
     남고, 800자로 잘라도 5명이 빠진다(싱어 379자 등). "수상자 전원"을 담는 것을 우선했다.
     800자 기준은 작품·공통 문서에만 적용하고, 짧은 수상자는 _짧은수상자 에 적어 둔다.
  ② 2홉은 두 갈래다. 작품(작가당 최대 3편) + 여러 시드가 함께 가리키는 공통 문서.
     작품은 "〈작가〉의 소설" 같은 분류로 주인을 확인한다(owns_work). 공통 문서에서 직업명은 뺀다.
  ③ 링크는 본문(extract)에 제목이 실제로 나오는 것만 남긴다. 수상자 문서 하단의 틀(navbox)이
     수상자 전원을 서로 이어 놓아서, prop=links 그대로 쓰면 수상자끼리 완전 그래프가 된다.
  ④ 위키백과는 살아 있는 소스라 다시 모을 때마다 결과가 달라진다. 한 번 모으면 고정하고
     --refresh 를 줄 때만 다시 모은다. API 응답은 .cache/wiki/ 에 저장해 다시 돌릴 때 재사용한다
     (429 로 수십 초씩 기다리는 일이 잦다). 캐시를 비우려면 --no-cache.

저장 형식 — 딕셔너리 둘이 본체이고, 밑줄로 시작하는 키는 설명용 메타데이터다.
  {"docs": {제목: 본문}, "links": {제목: [docs 안의 다른 제목, ...]},
   "_종류": {제목: "수상자"|"작품"|"공통"}, "_작가": {작품 제목: 수상자 제목}, ...}
"""
import argparse
import hashlib
import json
import re
import statistics
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path

import requests

# 노벨 문학상 수상자 122명 — 2026-09-23 에 한 번 뽑아 고정했다.
#   · 분류:노벨 문학상 수상자 의 문서 121건 (목록 문서 제외)
#   · + 공식 명단(api.nobelprize.org)과 대조해 찾은 분류 누락 1명 (2025년 수상자)
# 명단을 바꿀 때는 공식 명단과 먼저 대조해 차이를 보고(대조 스크립트는 개발용이라 저장소에 없다), 사람이 확인한 뒤 여기를 고친다.
SEEDS = [
    "가오싱젠", "가와바타 야스나리", "네이딘 고디머", "윌리엄 골딩", "존 골즈워디", "압둘라자크 구르나", "귄터 그라스", "루이즈 글릭",
    "카를 아돌프 기엘레루프", "V. S. 나이폴", "파블로 네루다", "그라치아 델레다", "밥 딜런", "페르 라게르크비스트", "셀마 라겔뢰프",
    "하들도르 락스네스", "버트런드 러셀", "도리스 레싱", "브와디스와프 레이몬트", "로맹 롤랑", "싱클레어 루이스", "장마리 귀스타브 르 클레지오",
    "가브리엘 가르시아 마르케스", "로제 마르탱 뒤 가르", "하뤼 마르틴손", "모리스 마테를링크", "나기브 마푸즈", "토마스 만", "앨리스 먼로",
    "파트리크 모디아노", "토니 모리슨", "프랑수아 모리아크", "모옌", "에우제니오 몬탈레", "테오도어 몸젠", "헤르타 뮐러", "가브리엘라 미스트랄",
    "프레데리크 미스트랄", "체스와프 미워시", "마리오 바르가스 요사", "펄 S. 벅", "하신토 베나벤테", "앙리 베르그송", "사뮈엘 베케트",
    "솔 벨로", "하인리히 뵐", "이반 부닌", "조지프 브로드스키", "비에른스티에르네 비에른손", "주제 사라마구", "장폴 사르트르", "야로슬라프 사이페르트",
    "요르고스 세페리스", "카밀로 호세 셀라", "월레 소잉카", "알렉산드르 솔제니친", "조지 버나드 쇼", "미하일 숄로호프", "카를 슈피텔러",
    "존 스타인벡", "클로드 시몽", "헨리크 시엔키에비치", "프란스 에밀 실란패", "비스와바 심보르스카", "아이작 바셰비스 싱어", "슈무엘 요세프 아그논",
    "아니 에르노", "미겔 앙헬 아스투리아스", "이보 안드리치", "비센테 알레익산드레", "스베틀라나 알렉시예비치", "호세 에체가라이", "T. S. 엘리엇",
    "오디세아스 엘리티스", "W. B. 예이츠", "요하네스 빌헬름 옌센", "엘프리데 옐리네크", "유진 오닐", "오에 겐자부로", "루돌프 크리스토프 오이켄",
    "에위빈드 욘손", "시그리드 운세트", "데릭 월컷", "가즈오 이시구로", "넬리 작스", "앙드레 지드", "윈스턴 처칠", "엘리아스 카네티",
    "조수에 카르두치", "에리크 악셀 카를펠트", "알베르 카뮈", "케르테스 임레", "살바토레 콰시모도", "존 맥스웰 쿳시", "러디어드 키플링",
    "라빈드라나트 타고르", "올가 토카르추크", "토마스 트란스트뢰메르", "오르한 파무크", "옥타비오 파스", "보리스 파스테르나크", "생존 페르스",
    "다리오 포", "욘 포세", "윌리엄 포크너", "헨리크 폰토피단", "아나톨 프랑스", "쉴리 프뤼돔", "루이지 피란델로", "해럴드 핀터",
    "게르하르트 하웁트만", "파울 요한 루트비히 폰 하이제", "한강 (작가)", "페터 한트케", "크누트 함순", "어니스트 헤밍웨이", "헤르만 헤세",
    "베르네르 폰 헤이덴스탐", "패트릭 화이트", "셰이머스 히니", "후안 라몬 히메네스",
    "크러스너호르커이 라슬로",   # 2025년 수상 — 분류에 아직 없어 공식 명단 대조로 추가
]

MIN_CHARS = 800          # 작품·공통 문서의 토막글 기준 (강의 프롬프트는 3,000자 — 위 docstring ① 참고)
WORKS_PER_AUTHOR = 3     # 작가당 작품 상한
COMMON_TOP = 12          # 공통 문서 상한
COMMON_MIN_SEEDS = 5     # 공통 문서는 시드 5명 이상이 본문에서 가리켜야 후보가 된다
MODEL_WINDOW = 128_000   # gpt-4o-mini 컨텍스트 창(토큰)

API = "https://ko.wikipedia.org/w/api.php"
# User-Agent 는 아스키로만 쓴다. 한글을 넣으면 인코딩 오류가 난다.
UA = "my-deep-researcher/0.1 (modulabs project; contact: byeunya at gmail dot com)"
PAUSE = 0.6              # 0.3초로는 429(요청 과다)가 났다 — my-graph-agent 에서 겪은 일
MAX_RETRY = 6
OUT = Path(__file__).parent / "data" / "corpus.json"
CACHE = Path(__file__).parent / ".cache" / "wiki"
USE_CACHE = True

# 연도 · 목록 · 틀 · 분류 문서는 후보에서 뺀다
EXCLUDE = [r"^\d+년", r"^\d+세기", r"목록", r"^틀:", r"^분류:", r"연표", r"^위키백과:"]
# 작품 판정: 분류에 작품 표지가 있고, 사람 표지가 없어야 한다
WORK_CAT = re.compile(r"(소설$|장편 소설|단편 소설|소설 작품|시집|희곡|의 작품|년 책|문학 작품|수필집|회고록)")
PERSON_CAT = re.compile(r"(소설가|시인|극작가|작가$|작가\)|출생|사망|사람|수상자|인물)")
# 공통 문서는 문학과 닿아 있는 분류만 받는다 (국가·도시 같은 거대 문서가 1위를 차지하지 않도록)
COMMON_CAT = re.compile(r"(문학|문예|사조|노벨|한림원|철학|소설|희곡|시학|운동$)")
# 직업명 문서는 공통에서 뺀다. 수상자 대부분이 "…의 소설가이다"라고 써서 공통 1·2위가 되지만
# 정작 수상자에 대한 내용은 없다 (첫 수집에서 소설가 42곳 · 작가 27곳이 가리켰다).
COMMON_EXCLUDE = {"소설가", "작가", "시인", "극작가", "수필가"}
# "헤르만 헤세의 소설", "단테 알리기에리의 작품" 처럼 누구의 작품인지 적은 분류
AUTHORED_CAT = re.compile(r"^(.+)의 (작품|소설|시집|희곡|시|책|수필|단편집)$")
LEAD_CHARS = 200         # 작가 분류가 없는 작품은 첫 200자(도입부)에 작가가 나와야 한다

SESSION = requests.Session()
SESSION.headers["User-Agent"] = UA
# 넘겨주기(redirect) 표. prop=links 는 링크를 적힌 그대로 돌려주므로 정식 제목으로 바꿀 때 쓴다.
REDIRECTS = {}


# ─── MediaWiki 호출 ──────────────────────────────────────────────

def _get(params):
    """캐시에 있으면 그것을 쓰고, 없으면 친다. 429·5xx 는 기다렸다 다시 친다(Retry-After 를 따른다)."""
    key = hashlib.sha1(json.dumps(params, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    path = CACHE / f"{key}.json"
    if USE_CACHE and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    data = _fetch(params)
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    time.sleep(PAUSE)
    return data


def _fetch(params, url=API):
    """429·5xx·연결 오류는 기다렸다 다시 친다. 명단 대조 스크립트도 위키데이터 호출에 이것을 쓴다."""
    for attempt in range(MAX_RETRY):
        try:
            r = SESSION.get(url, params=params, timeout=30)
        except requests.RequestException as e:
            wait = 2 ** attempt
            print(f"    … 연결 오류 {type(e).__name__} — {wait}초 뒤 재시도")
            time.sleep(wait)
            continue
        if r.status_code in (429, 502, 503, 504):
            wait = float(r.headers.get("Retry-After", 2 ** (attempt + 1)))
            print(f"    … {r.status_code} — {wait:.0f}초 쉬고 재시도 ({attempt + 1}/{MAX_RETRY})")
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"재시도 {MAX_RETRY}번 모두 실패: {params.get('titles', '')[:60]}")


def api(**params):
    """응답에 continue 가 있으면 이어 받아 제목별로 합친다."""
    params.update(action="query", format="json", formatversion=2, redirects=1)
    merged, cont = {}, {}
    while True:
        data = _get({**params, **cont})
        for r in data.get("query", {}).get("normalized", []) + data.get("query", {}).get("redirects", []):
            REDIRECTS[r["from"]] = r["to"]
        for page in data.get("query", {}).get("pages", []):
            slot = merged.setdefault(page["title"], {})
            for key, value in page.items():
                if isinstance(value, list):
                    slot.setdefault(key, []).extend(value)
                else:
                    slot[key] = value
        if "continue" not in data:
            return merged
        cont = data["continue"]


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def canon(title):
    """넘겨주기를 따라가 정식 제목을 돌려준다 (두 번 넘어가는 경우까지)."""
    for _ in range(3):
        if title not in REDIRECTS:
            break
        title = REDIRECTS[title]
    return title


def get_links(titles):
    """제목 -> 그 문서가 가리키는 본문 네임스페이스 링크 전부 (틀 속 링크도 섞여 있다)."""
    out = {}
    for batch in chunks(titles, 20):
        for title, page in api(prop="links", plnamespace=0, pllimit="max",
                               titles="|".join(batch)).items():
            out[title] = [canon(l["title"]) for l in page.get("links", [])]
    return out


def get_meta(titles):
    """제목 -> (분류 목록, 위키텍스트 바이트 길이). 없는 문서는 빠진다."""
    out = {}
    for batch in chunks(titles, 50):         # 분류·길이는 50건씩 묶어 받을 수 있다
        for title, page in api(prop="categories|info", cllimit="max", clshow="!hidden",
                               titles="|".join(batch)).items():
            if page.get("missing"):
                continue
            cats = [c["title"].removeprefix("분류:") for c in page.get("categories", [])]
            out[title] = (cats, page.get("length", 0))
    return out


def get_extract(title):
    """본문 평문. extracts 는 한 번에 한 문서만 받을 수 있다."""
    pages = api(prop="extracts", explaintext=1, exlimit=1, titles=title)
    page = pages.get(title) or next(iter(pages.values()), {})
    return page.get("extract", "") or ""


# ─── 판정 규칙 (순수 함수 — tests/test_collect.py 가 검사한다) ────────────

def base_name(title):
    """'페스트 (소설)' -> '페스트'. 본문에는 괄호 속 구분자 없이 나온다."""
    return re.sub(r"\s*\([^)]*\)$", "", title).strip()


def is_excluded(title):
    return any(re.search(p, title) for p in EXCLUDE)


def mentioned(title, text):
    """링크 제목이 본문에 실제로 나오는가. 한 글자 제목은 우연히 겹치기 쉬워 뺀다."""
    name = base_name(title)
    return len(name) >= 2 and name in text


def is_work(categories):
    return (any(WORK_CAT.search(c) for c in categories)
            and not any(PERSON_CAT.search(c) for c in categories))


def name_tokens(author):
    """역링크 판정용. '알베르 카뮈' -> ['알베르', '카뮈'], 'T. S. 엘리엇' -> ['엘리엇']."""
    return [t for t in base_name(author).replace(".", " ").split() if len(t) >= 2]


def mentions_author(author, text):
    """본문이 작가를 가리키는가 — 성이든 이름이든 한 토막이라도 나오면 된다."""
    return any(t in text for t in name_tokens(author))


def owns_work(author, categories, text):
    """이 문서가 정말 그 작가의 작품인가.

    ① "〈작가〉의 소설/작품…" 분류가 있으면 그 작가의 것이다.
    ② 다른 사람 이름의 그런 분류가 있으면 남의 작품이다 (카뮈 문서가 가리킨 『자본론』,
       오에 겐자부로 문서가 가리킨 『신곡』 같은 것).
    ③ 누구의 것인지 적힌 분류가 없으면, 도입부 첫 200자에 작가가 나와야 한다.
       본문 아무 데나 나오면 된다고 했더니 '아나톨 프랑스'의 '프랑스'가 브리태니커 백과사전에
       걸렸다. 도입부는 보통 "《데미안》은 헤르만 헤세가 발표한 소설이다"처럼 작가를 밝힌다.
    """
    name = base_name(author)
    owners = [m.group(1) for c in categories if (m := AUTHORED_CAT.match(c))]
    # 나라 이름 분류("미국의 책", "프랑스의 시")는 사람이 아니므로 작가 판정에 쓰지 않는다
    people = [o for o in owners if " " in o or o == name]
    if name in people:
        return True
    if people:
        return False
    return mentions_author(author, text[:LEAD_CHARS])


def body_links(raw_links, text, pool):
    """raw_links 중 pool(코퍼스 안) 에 있고 본문에 실제로 나오는 것만. 순서는 유지, 중복 제거."""
    seen, out = set(), []
    for t in raw_links:
        if t in pool and t not in seen and mentioned(t, text):
            seen.add(t)
            out.append(t)
    return out


# ─── 수집 ───────────────────────────────────────────────────────

def collect():
    print(f"① 시드 {len(SEEDS)}건 — 링크와 본문을 받는다")
    seed_links = get_links(SEEDS)
    texts, short = {}, []
    for i, s in enumerate(SEEDS, 1):
        body = get_extract(s)
        if not body:
            raise RuntimeError(f"시드 본문이 비었다: {s} — 제목이 바뀌었는지 확인")
        texts[s] = body                      # 수상자는 길이와 상관없이 넣는다 (docstring ①)
        if len(body) < MIN_CHARS:
            short.append((s, len(body)))
        if i % 20 == 0:
            print(f"    본문 {i}/{len(SEEDS)}")
    seeds_kept = list(texts)
    print(f"    시드 채택 {len(seeds_kept)} · 그중 {MIN_CHARS}자 미만 {len(short)}: {short}")

    # 시드 본문에 실제로 나오는 링크만 2홉 후보가 된다
    hop = {s: [t for t in seed_links.get(s, [])
               if t not in SEEDS and not is_excluded(t) and mentioned(t, texts[s])]
           for s in seeds_kept}
    candidates = sorted({t for ts in hop.values() for t in ts})
    print(f"② 2홉 후보 {len(candidates)}건 — 분류와 길이를 받는다")
    meta = get_meta(candidates)
    # 후보가 넘겨주기였으면 정식 제목으로 바꾼다 (get_meta 가 REDIRECTS 를 채운다)
    hop = {s: list(dict.fromkeys(canon(t) for t in ts)) for s, ts in hop.items()}

    print(f"③ 작품 — 작가당 최대 {WORKS_PER_AUTHOR}편 (작품 분류 · 작가 분류 또는 도입부 언급 · {MIN_CHARS}자 이상)")
    author_of = {}
    for s in seeds_kept:
        works = [t for t in hop[s] if t in meta and is_work(meta[t][0]) and t not in author_of]
        works.sort(key=lambda t: -meta[t][1])      # 위키텍스트가 긴 것부터
        taken = 0
        for w in works:
            if taken >= WORKS_PER_AUTHOR:
                break
            body = get_extract(w)
            if len(body) >= MIN_CHARS and owns_work(s, meta[w][0], body):
                texts[w] = body
                author_of[w] = s
                taken += 1
    print(f"    작품 채택 {len(author_of)}건")

    print(f"④ 공통 문서 — 시드 {COMMON_MIN_SEEDS}명 이상이 함께 가리키는 문학 관련 문서 상위 {COMMON_TOP}건")
    counts = Counter(t for ts in hop.values() for t in set(ts))
    commons = []
    for t, n in counts.most_common():
        if len(commons) >= COMMON_TOP or n < COMMON_MIN_SEEDS:
            break
        if t in texts or t not in meta or t in COMMON_EXCLUDE:
            continue
        cats = meta[t][0]
        if is_work(cats) or not any(COMMON_CAT.search(c) for c in cats):
            continue
        body = get_extract(t)
        if len(body) >= MIN_CHARS:
            texts[t] = body
            commons.append((t, n))
    print(f"    공통 채택 {len(commons)}건: {commons}")

    print("⑤ 링크 — 작품·공통 문서의 링크를 받고, 코퍼스 안 + 본문에 나오는 것만 남긴다")
    pool = set(texts)
    raw = dict(seed_links)
    raw.update(get_links([t for t in texts if t not in seed_links]))
    raw = {t: [canon(l) for l in ls] for t, ls in raw.items()}
    links = {t: body_links(raw.get(t, []), texts[t], pool - {t}) for t in texts}

    kinds = {t: ("수상자" if t in SEEDS else "작품" if t in author_of else "공통") for t in texts}
    return {
        "_설명": "노벨 문학상 수상자·대표작·공통 문서. docs=본문 평문, links=본문에 실제로 나오는 코퍼스 안 링크",
        "_출처": "한국어 위키백과 (CC BY-SA 4.0), MediaWiki API 로 수집",
        "_수집일": date.today().isoformat(),
        "_기준": {"토막글_최소자수": MIN_CHARS, "작가당_작품": WORKS_PER_AUTHOR,
                  "공통_상한": COMMON_TOP, "공통_최소시드": COMMON_MIN_SEEDS},
        "_짧은수상자": short,
        "_종류": kinds,
        "_작가": author_of,
        "docs": texts,
        "links": links,
    }


# ─── 통계 ───────────────────────────────────────────────────────

def count_tokens(texts):
    try:
        import tiktoken
    except ImportError:
        return None
    enc = tiktoken.get_encoding("o200k_base")   # gpt-4o 계열 토크나이저
    return sum(len(enc.encode(t)) for t in texts)


def report(corpus):
    docs, links = corpus["docs"], corpus["links"]
    total = sum(len(t) for t in docs.values())
    tokens = count_tokens(docs.values())
    zero = sorted(t for t in docs if not links.get(t))
    kinds = Counter(corpus.get("_종류", {}).values())
    print("\n── 코퍼스 통계 ──")
    print(f"문서 수            {len(docs)}  ({dict(kinds)})")
    print(f"총 글자 수          {total:,}")
    if tokens is not None:
        verdict = "넘는다" if tokens > MODEL_WINDOW else "넘지 않는다"
        print(f"총 토큰 수          {tokens:,}  → 모델 창 {MODEL_WINDOW:,} 의 {tokens / MODEL_WINDOW:.1f}배, 창을 {verdict}")
    print(f"문서당 링크 수 중앙값 {statistics.median(len(links.get(t, [])) for t in docs)}")
    print(f"링크가 0개인 문서    {len(zero)}건: {zero}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--refresh", action="store_true", help="있는 코퍼스를 버리고 다시 모은다")
    ap.add_argument("--stats", action="store_true", help="통계만 출력한다")
    ap.add_argument("--no-cache", action="store_true", help="API 응답 캐시를 쓰지 않는다")
    args = ap.parse_args()
    global USE_CACHE
    USE_CACHE = not args.no_cache

    if OUT.exists() and not args.refresh:
        print(f"{OUT.name} 가 이미 있다 — 다시 모으려면 --refresh")
        report(json.loads(OUT.read_text(encoding="utf-8")))
        return
    if args.stats:
        sys.exit(f"{OUT} 가 없다. 먼저 python collect_corpus.py")

    corpus = collect()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(corpus, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n저장: {OUT}")
    report(corpus)


if __name__ == "__main__":
    main()
