#!/usr/bin/env python3
"""기획만 따로 재는 실험 — 코디네이터의 목차가 얼마나 흔들리는지, 기획을 고친 뒤 나아졌는지.

  python plan_check.py --label 전                 # Q2·Q5·Q8 × 5회, 기획만 (서브에이전트는 돌지 않는다)
  python plan_check.py --label 후 --repeats 5
  python plan_check.py --summarize-only           # LLM 없이 output/plans.jsonl 에서 표만

전 구간 실험(ablation.py)은 한 번에 LLM 을 수십 번 부르지만, 기획은 한두 번이면 된다. 기획을 고칠 때는
이것으로 먼저 재고, 나아졌을 때만 전 구간 실험으로 넘어간다.

재는 것(모두 목차와 교정 기록에서 코드로 센다)
  재기획 발동 · 채택   — 첫 목차에 문제가 있어 다시 짜게 한 횟수, 그중 다시 짠 목차를 쓴 횟수
  빈 절 제외          — 담당문서가 하나도 남지 않아 파견하지 않은 절이 생긴 횟수
  시대불일치          — 절 제목에 기간(1951-2000)이 있는데 그 기간 밖에 상을 받은 수상자를 담당시킨 건수
  구역겹침 · 배정실패  — 최종 목차에서 코드가 막은 건수 (재기획 전 목차의 문제는 세지 않는다)
  베낌               — 카드 줄('제목 ｜ 1993년 수상')을 제목째 베껴 와 코드가 맞춘 건수
  형식의심 절         — 절 제목이 '생애 · 사상 · 시대적 배경 · 주제 변화'처럼 역할/형식 축으로 보이는 절 (사람이 확인할 후보)
  목차 일관성         — 같은 질문 N회의 담당문서 집합(공용 서가 제외)끼리 겹치는 정도(자카드 평균, 0~1)
  코디 글자           — 코디네이터가 기획에 본 글자 (격리 비용 — 기획을 고쳐도 늘면 안 된다)
"""
import argparse
import itertools
import json
import re
import statistics
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import graph
from titles import era_mismatches, era_range

OUT = graph.OUTPUT / "plans.jsonl"
SUMMARY = graph.OUTPUT / "plans.json"
QUESTIONS = ["Q2", "Q5", "Q8"]

_FORMATISH = re.compile(r"(생애|사상|시대적 배경|사회적 맥락|역사적 배경|주제 변화|주제와|문학적 흐름|문학적 기여|작품과 주제|영향과 의의)")


def stats(rec):
    """기획 한 번의 기록 → 숫자."""
    plan, cost = rec["plan"], rec["cost"]
    fixes = plan.get("교정", [])
    final = [f for f in fixes if not f.startswith("(재기획 채택)")]
    toc = plan.get("목차", [])
    era_total = sum(1 for t in toc if era_range(t["절"]) for d in t["담당문서"] if d in graph.AWARD_YEARS)
    era_bad = era_mismatches(toc, graph.AWARD_YEARS)
    return {
        "재기획발동": int(any(c.get("단계") == "기획(재)" for c in cost)),
        "재기획채택": int(any(f.startswith("(재기획 채택)") for f in fixes)),
        "빈절제외": int(any("구역이 빈 절" in f for f in fixes)),
        "절수": len(toc),
        "시대절담당": era_total,
        "시대불일치": len(era_bad),
        "구역겹침": sum("구역 겹침" in f for f in final),
        "배정실패": sum("코퍼스에 없어" in f for f in final),
        "베낌": sum("년 수상" in f and "맞춤" in f for f in final),
        "형식의심절": sum(bool(_FORMATISH.search(t["절"])) for t in toc),
        "코디글자": sum(c["글자"] for c in cost if c.get("누가") == "코디"),
    }


def territory(rec):
    return {d for t in rec["plan"].get("목차", []) for d in t["담당문서"] if d not in graph.SHARED}


def consistency(recs):
    """같은 질문 여러 회의 담당문서 집합끼리 자카드 평균. 2회 미만이면 None."""
    sets = [territory(r) for r in recs]
    pairs = [len(a & b) / len(a | b) for a, b in itertools.combinations(sets, 2) if a | b]
    return round(statistics.mean(pairs), 2) if pairs else None


def load(label=None):
    if not OUT.exists():
        return []
    recs = [json.loads(line) for line in OUT.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in recs if label is None or r["라벨"] == label]


def run_one(label, qid, rep):
    _, question = graph.load_question(qid)
    out = graph.run(question, plan_only=True)
    rec = {"라벨": label, "질문id": qid, "반복": rep, "시각": time.strftime("%Y-%m-%d %H:%M:%S"),
           "코퍼스": graph.corpus_version(), "question": question,
           "plan": out["plan"], "cost": out["cost"], "log": out["log"]}
    with graph._save_lock:
        with OUT.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    s = stats(rec)
    print(f"  [{label}] {qid} r{rep} · 절 {s['절수']} · 재기획 {'채택' if s['재기획채택'] else ('무효' if s['재기획발동'] else '-')}"
          f" · 빈절 {s['빈절제외']} · 시대불일치 {s['시대불일치']}/{s['시대절담당']} · 겹침 {s['구역겹침']}", flush=True)
    return rec


def summarize():
    recs = load()
    summary = {}
    for label in dict.fromkeys(r["라벨"] for r in recs):
        summary[label] = {}
        for qid in dict.fromkeys(r["질문id"] for r in recs if r["라벨"] == label):
            group = [r for r in recs if r["라벨"] == label and r["질문id"] == qid]
            ss = [stats(r) for r in group]
            tot = lambda k: sum(s[k] for s in ss)
            summary[label][qid] = {
                "n": len(group),
                "재기획": f"{tot('재기획발동')}회 중 채택 {tot('재기획채택')}",
                "빈절제외": tot("빈절제외"),
                "절수": dict(sorted(Counter(s["절수"] for s in ss).items())),
                "시대불일치": f"{tot('시대불일치')}/{tot('시대절담당')}",
                "구역겹침": tot("구역겹침"), "배정실패": tot("배정실패"), "베낌": tot("베낌"),
                "형식의심절": tot("형식의심절"),
                "일관성": consistency(group),
                "코디글자": round(statistics.mean(s["코디글자"] for s in ss)),
                "목차": [" / ".join(t["절"] for t in r["plan"]["목차"]) for r in group],
            }
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary


def print_table(summary):
    cols = ["n", "재기획", "빈절제외", "절수", "시대불일치", "구역겹침", "배정실패", "베낌", "형식의심절",
            "일관성", "코디글자"]
    for label, qs in summary.items():
        print(f"\n── {label}")
        for qid, v in qs.items():
            print(f"  {qid}  " + " · ".join(f"{c} {v[c]}" for c in cols))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", default="기본", help="이번 측정의 이름 (예: 전 / 후)")
    ap.add_argument("--questions", nargs="+", default=QUESTIONS)
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--summarize-only", action="store_true")
    args = ap.parse_args()
    if not args.summarize_only:
        done = {(r["질문id"], r["반복"]) for r in load(args.label)}
        jobs = [(q, rep) for q in args.questions for rep in range(1, args.repeats + 1) if (q, rep) not in done]
        print(f"기획만 {len(jobs)}번 — 라벨 '{args.label}'")
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(lambda j: run_one(args.label, *j), jobs))
    print_table(summarize())
    print(f"\n저장: {graph._rel(OUT)} · {graph._rel(SUMMARY)}")


if __name__ == "__main__":
    main()
