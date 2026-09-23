#!/usr/bin/env python3
"""수상자 명단 점검 — 노벨상 공식 명단과 collect_corpus.SEEDS 를 비교해 차이만 보고한다.

  python check_seeds.py            # 비교 결과를 출력하고 data/seed_check.json 에 남긴다

SEEDS 는 한국어 위키백과 분류에서 뽑았기 때문에, 분류가 늦게 붙거나 빠지면 수상자가 누락된다.
그래서 기준을 노벨상 공식 API(api.nobelprize.org)로 두고, 한국어 위키백과 제목은 공식 응답에
들어 있는 위키데이터 ID → 위키데이터 sitelink(kowiki) 로 찾는다.

이 스크립트는 **비교만 한다. SEEDS 를 고치지 않는다.** 결과를 사람이 확인한 뒤
collect_corpus.py 의 SEEDS 를 직접 고치고 `python collect_corpus.py --refresh` 로 다시 모은다.
코퍼스가 조용히 바뀌면 이미 돌린 실험과 비교할 수 없게 되기 때문이다.
"""
import json
import sys
import time
import unicodedata
from datetime import date
from pathlib import Path

from collect_corpus import SEEDS, _fetch

NOBEL_API = "https://api.nobelprize.org/2.1/laureates"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
OUT = Path(__file__).parent / "data" / "seed_check.json"
YEARS_OUT = Path(__file__).parent / "data" / "award_years.json"

def official_laureates():
    """노벨상 공식 API 의 문학상 수상자 전원. 한 사람이 두 번 받은 경우는 없지만 연도는 목록으로 둔다."""
    out, offset = [], 0
    while True:
        data = _fetch({"nobelPrizeCategory": "lit", "limit": 100, "offset": offset}, url=NOBEL_API)
        for l in data["laureates"]:
            name = (l.get("knownName") or l.get("orgName") or l.get("fullName") or {}).get("en", "")
            years = sorted(p["awardYear"] for p in l.get("nobelPrizes", [])
                           if p.get("category", {}).get("en") == "Literature")
            out.append({"id": l["id"], "이름": name, "성": (l.get("familyName") or {}).get("en", ""),
                        "연도": years, "wikidata": (l.get("wikidata") or {}).get("id")})
        offset += len(data["laureates"])
        if not data["laureates"] and offset < data["meta"]["count"]:
            print(f"⚠ 공식 API 가 {data['meta']['count']}명 중 {offset}명에서 빈 페이지를 돌려줬다 — 명단이 모자랄 수 있다")
        if offset >= data["meta"]["count"] or not data["laureates"]:
            return out
        time.sleep(0.5)


def kowiki_titles(qids):
    """위키데이터 ID -> 한국어 위키백과 문서 제목 (없으면 빠진다). 한 번에 50개씩."""
    out = {}
    qids = [q for q in qids if q]
    for i in range(0, len(qids), 50):
        data = _fetch({"action": "wbgetentities", "ids": "|".join(qids[i:i + 50]),
                       "props": "sitelinks", "sitefilter": "kowiki", "format": "json"},
                      url=WIKIDATA_API)
        for qid, ent in data.get("entities", {}).items():
            link = ent.get("sitelinks", {}).get("kowiki")
            if link:
                out[qid] = link["title"]
        time.sleep(0.5)
    return out


def seed_english_names(titles):
    """한국어 위키백과 제목 -> 그 문서에 연결된 위키데이터 항목의 영어 이름(레이블 + enwiki 제목)."""
    out = {}
    for i in range(0, len(titles), 50):
        data = _fetch({"action": "wbgetentities", "sites": "kowiki", "titles": "|".join(titles[i:i + 50]),
                       "props": "labels|sitelinks", "languages": "en", "sitefilter": "kowiki|enwiki",
                       "format": "json"}, url=WIKIDATA_API)
        for qid, ent in data.get("entities", {}).items():
            ko_title = ent.get("sitelinks", {}).get("kowiki", {}).get("title")
            if ko_title:
                out[ko_title] = [ent.get("labels", {}).get("en", {}).get("value", ""),
                                 ent.get("sitelinks", {}).get("enwiki", {}).get("title", "")]
        time.sleep(0.5)
    return out


def _norm(text):
    """악센트·대소문자·구두점을 지워 이름을 견준다. 'Le Clézio' -> 'le clezio'."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join("".join(c if c.isalnum() else " " for c in text.lower()).split())


def pair_by_name(no_ko, extra, english):
    """공식 명단의 위키데이터 ID 가 틀린 경우를 구제한다.

    '한국어 문서 없음'으로 떨어진 공식 수상자와 '공식 명단에 없는 SEEDS' 를, SEEDS 쪽 영어 이름에
    공식 명단의 성(familyName)이 들어 있으면 짝짓는다. 짝지은 것은 사람이 확인할 몫으로 따로 보고한다.
    """
    pairs, left_official, left_seeds = [], [], list(extra)
    for r in no_ko:
        family = _norm(r.get("성") or r["이름"].split()[-1])
        hit = next((t for t in left_seeds
                    if family and any(family in _norm(n) for n in english.get(t, []))), None)
        if hit:
            pairs.append({**r, "kowiki": hit, "근거": english[hit]})
            left_seeds.remove(hit)
        else:
            left_official.append(r)
    return pairs, left_official, left_seeds


def compare(official, ko, seeds):
    """세 갈래로 나눈다 — 추가 후보 · 한국어 문서 없음 · 공식 명단에 없는 SEEDS."""
    seeds = set(seeds)
    matched, to_add, no_ko = [], [], []
    for l in official:
        title = ko.get(l["wikidata"])
        row = {**l, "kowiki": title}
        if title is None:
            no_ko.append(row)
        elif title in seeds:
            matched.append(row)
        else:
            to_add.append(row)
    official_titles = {r["kowiki"] for r in matched + to_add}
    extra = sorted(seeds - official_titles)
    return {"일치": matched, "추가_후보": to_add, "한국어_문서_없음": no_ko, "공식_명단에_없음": extra}


def award_years(result):
    """한국어 문서 제목 -> 수상 연도 목록. 공식 명단과 짝지어진 수상자만 (graph.py 의 문서 카드가 쓴다)."""
    rows = result.get("일치", []) + result.get("공식ID_의심", [])
    return dict(sorted(((r["kowiki"], r["연도"]) for r in rows), key=lambda kv: (kv[1], kv[0])))


def main():
    official = official_laureates()
    ko = kowiki_titles([l["wikidata"] for l in official])
    result = compare(official, ko, SEEDS)
    english = seed_english_names(result["공식_명단에_없음"]) if result["공식_명단에_없음"] else {}
    pairs, result["한국어_문서_없음"], result["공식_명단에_없음"] = pair_by_name(
        result["한국어_문서_없음"], result["공식_명단에_없음"], english)
    result["공식ID_의심"] = pairs

    print(f"공식 명단 {len(official)}명 · SEEDS {len(SEEDS)}명 · 일치 {len(result['일치'])}명"
          f" · 이름으로 짝지음 {len(result['공식ID_의심'])}명")
    print(f"\n① 추가 후보 (공식 명단에 있고 한국어 문서도 있는데 SEEDS 에 없음) — {len(result['추가_후보'])}명")
    for r in result["추가_후보"]:
        print(f"    {'·'.join(r['연도'])}  {r['이름']}  →  \"{r['kowiki']}\"")
    print(f"\n② 한국어 문서 없음 (넣을 수 없다 — REPORT 에 누락으로 기록) — {len(result['한국어_문서_없음'])}명")
    for r in result["한국어_문서_없음"]:
        print(f"    {'·'.join(r['연도'])}  {r['이름']}  (wikidata {r['wikidata']})")
    print(f"\n③ 공식 데이터의 위키데이터 ID 가 의심됨 — 이름으로 짝지음, 확인 필요 — {len(result['공식ID_의심'])}명")
    for r in result["공식ID_의심"]:
        print(f"    {'·'.join(r['연도'])}  {r['이름']}  (공식 wikidata {r['wikidata']})  →  \"{r['kowiki']}\"  근거 {r['근거']}")
    print(f"\n④ 공식 명단에 없는 SEEDS (제목이 바뀌었거나 분류가 잘못 붙음) — {len(result['공식_명단에_없음'])}건")
    for t in result["공식_명단에_없음"]:
        print(f"    {t}")

    OUT.write_text(json.dumps({"_점검일": date.today().isoformat(), "_출처": NOBEL_API,
                               **result}, ensure_ascii=False, indent=1), encoding="utf-8")
    YEARS_OUT.write_text(json.dumps({"_출처": NOBEL_API, "_점검일": date.today().isoformat(),
                                     "연도": award_years(result)}, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    print(f"\n저장: {OUT}  (SEEDS 는 바꾸지 않았다)")
    print(f"저장: {YEARS_OUT}  (수상 연도 {len(result['일치']) + len(result['공식ID_의심'])}명 — 문서 카드에 쓰인다)")
    return 0 if not (result["추가_후보"] or result["공식_명단에_없음"]) else 1


if __name__ == "__main__":
    sys.exit(main())
