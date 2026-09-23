#!/usr/bin/env python3
"""절제 실험 — 스위치를 하나씩 끄고 같은 질문을 여러 번 돌려, 무엇이 값을 했는지 가른다.

  python ablation.py --exp ablation-2       # 질문 Q2·Q5·Q8 × 설정 8개 × 3회 (끊겨도 다시 부르면 이어서)
  python ablation.py --exp ablation-3 --settings 기본 기획보강끔 --repeats 5   # 기획보강 켬/끔 → output/ablation-3.json
  python ablation.py --repeats 1 --questions Q5
  python ablation.py --summarize-only        # LLM 없이 runs.jsonl 에서 표만 다시 만든다
  python ablation.py --remeasure             # 지표 규칙을 고친 뒤 — 저장된 실행 전부의 지표를 LLM 없이 다시 잰다

설정마다 한 번만 돌리면 안 된다. 같은 설정도 시행마다 목차가 달라지고(S3 에서 Q5 목차가 세 번 다 달랐다)
지표가 십몇 %p 씩 움직인다. 그래서 반복하고, 평균과 함께 흔들림(표준편차)을 적는다.

혼자 하는 대조군은 같은 반복 회차의 '기본' 팀 실행이 실제로 읽은 글자만큼 예산을 받는다.
결과: output/runs.jsonl (실행마다 한 줄, '실험'·'반복' 표시) · output/ablation.json (ablation-2 요약) · output/<실험>.json
"""
import argparse
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import baseline
import graph
import metrics

SETTINGS = {                       # 이름: 기본값에서 바꾸는 스위치
    "기본": {},
    "역할끔": {"역할": False},
    "배정끔": {"배정": False},
    "배정끔+재촉": {"배정": False, "재촉": True},   # 배정끔의 낮은 예산 소진이 '배정 없음' 때문인지 '안 붙잡음' 때문인지
    "구역끔": {"구역": False},
    "담당구역켬": {"담당구역": True},  # 남의 담당문서까지 피하기
    "재위임끔": {"재위임": False},
    "기획보강끔": {"기획보강": False},   # ablation-3 — 제출본 기획(시대 검사 · 빈 절 지시 나눠 주기 없음)
    "혼자": None,                   # 대조군
}
ABLATION2 = [k for k in SETTINGS if k != "기획보강끔"]   # 제출본 실험의 설정 — --settings 를 안 주면 이것만 돈다
# 주의: ablation-1 의 '기본'은 담당구역을 켠 채로 돌았다(당시 기본값). 그 실험의 '담당구역끔'이 지금의 기본값이다.
LEGACY = {"ablation-1": "이 실험의 '기본'은 담당구역 켬(당시 기본값)으로 돌았다. '담당구역끔'이 현재 기본값과 같다. "
                        "S7 결과(차이가 표준편차 안)를 보고 담당구역 기본값을 끔으로 바꿨다."}
SIGNALS = ["근거율", "예산소진율", "중복률", "인용편중", "읽고안쓴비율", "읽은문서", "보고서자수",
           "LLM호출", "격리율", "코디카드열람률"]
ALARMS = [k for k, v in metrics.METRICS.items() if v[0] == "경보"]
RUNS = graph.OUTPUT / "runs.jsonl"
OUT = graph.OUTPUT / "ablation.json"   # 제출본의 최종 실험(ablation-2) — 데모 사이드바가 읽는다


def out_path(exp):
    """요약 파일 — ablation-2 는 ablation.json, 다른 실험은 제 이름으로(ablation-3.json) 따로 둔다."""
    return OUT if exp == "ablation-2" else graph.OUTPUT / f"{exp}.json"


def load_runs(exp):
    if not RUNS.exists():
        return []
    rows = [json.loads(line) for line in RUNS.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if r.get("실험") == exp]


def done_key(r):
    return (r["질문id"], r["설정"], r["반복"])


def run_one(exp, qid, setting, rep, team_base=None):
    """한 번 돌리고 저장한다. 혼자면 team_base(같은 회차 기본 실행)의 읽은 양을 예산으로."""
    _, question = graph.load_question(qid)
    extra = {"실험": exp, "반복": rep}
    if setting == "혼자":
        budget = sum(v[2] for v in team_base["visited"])
        out = baseline.solo(question, budget, len(team_base["sections"]))
        m, detail = metrics.measure(graph.record_of(out), graph.CORPUS)
        out["metrics"] = {**m, "_세부": detail, "_짝": team_base["id"]}
        graph.save_run(out, qid, label="혼자", kind="혼자", extra={**extra, "설정": "혼자"})
    else:
        out = graph.run(question, **SETTINGS[setting])
        graph.save_run(out, qid, label=setting, kind="팀", extra={**extra, "설정": setting})
    m = out["metrics"]
    return f"{qid} r{rep} {setting:<6} 근거율 {m['근거율']:>5}% · 읽은문서 {m['읽은문서']:>2} · 편중 {m['인용편중']:>5}%"


def run_question(exp, qid, repeats, settings):
    """질문 하나를 끝까지 — 회차마다 기본을 먼저 돌리고(대조군 예산의 기준) 나머지를 돌린다."""
    for rep in range(1, repeats + 1):
        for setting in settings:
            done = {done_key(r): r for r in load_runs(exp)}
            if (qid, setting, rep) in done:
                continue
            base = done.get((qid, "기본", rep))
            if setting == "혼자" and base is None:
                print(f"   {qid} r{rep} 혼자 — 같은 회차 기본 실행이 없어 건너뜀", flush=True)
                continue
            t0 = time.time()
            try:
                line = run_one(exp, qid, setting, rep, base)
                print(f"   {line} · {time.time() - t0:.0f}초", flush=True)
            except Exception as e:                   # 한 번 실패로 전체를 멈추지 않는다 — 다시 부르면 이어서 돈다
                print(f"   ✗ {qid} r{rep} {setting} 실패: {type(e).__name__}: {e}", flush=True)


def remeasure(path=None):
    """저장된 실행 전부의 지표를 지금의 metrics.measure 로 다시 잰다. 보고서 · 읽은 기록은 그대로다.

    지표 규칙을 고치면(예: «A, B» 를 두 건으로 세기) 저장된 숫자가 옛 규칙으로 남는다. LLM 을 다시 부르지 않고
    기록만으로 다시 계산해 덮는다 — measure 가 기록만으로 계산되게 만든 이유가 이것이다.
    """
    path = path or RUNS
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    changed = 0
    for r in rows:
        m, detail = metrics.measure({**r, "all_drafts": r.get("원고전부", r["sections"])}, graph.CORPUS)
        keep = {k: v for k, v in r.get("metrics", {}).items() if k.startswith("_") and k != "_세부"}
        new = {**m, "_세부": detail, **keep}
        changed += any(r.get("metrics", {}).get(k) != v for k, v in m.items())
        r["metrics"] = new
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return len(rows), changed


def summarize(exp):
    rows = load_runs(exp)
    table = {}
    for r in rows:
        table.setdefault(r["질문id"], {}).setdefault(r["설정"], []).append(r)
    summary = {}
    for qid, by_setting in sorted(table.items()):
        summary[qid] = {}
        order = list(SETTINGS) + sorted(k for k in by_setting if k not in SETTINGS)
        for setting in order:
            runs = by_setting.get(setting, [])
            if not runs:
                continue
            ms = [r["metrics"] for r in runs]
            entry = {"n": len(runs), "실행": [r["id"] for r in runs]}
            for k in SIGNALS:
                vals = [m[k] for m in ms]
                entry[k] = {"평균": round(statistics.mean(vals), 1),
                            "표준편차": round(statistics.stdev(vals), 1) if len(vals) > 1 else 0.0,
                            "값": vals}
            entry["경보합"] = {k: sum(m[k] for m in ms) for k in ALARMS if sum(m[k] for m in ms)}
            summary[qid][setting] = entry
    out_path(exp).write_text(json.dumps({"실험": exp, "만든시각": time.strftime("%Y-%m-%d %H:%M"),
                               "주의": LEGACY.get(exp, ""),
                               "지표설명": {k: metrics.METRICS[k] for k in SIGNALS + ALARMS},
                               "결과": summary}, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary


def print_table(summary):
    cols = ["근거율", "읽은문서", "인용편중", "중복률", "예산소진율", "보고서자수", "LLM호출", "격리율"]
    for qid, by_setting in summary.items():
        print(f"\n■ {qid}")
        print(f"{'설정':<8}{'n':>3} " + " ".join(f"{c:>12}" for c in cols) + "   경보(합)")
        for setting, e in by_setting.items():
            cells = [f"{e[c]['평균']:>7}±{e[c]['표준편차']:<4}" for c in cols]
            alarms = " ".join(f"{k}{v}" for k, v in e["경보합"].items()) or "-"
            print(f"{setting:<8}{e['n']:>3} " + " ".join(f"{c:>12}" for c in cells) + f"   {alarms}")


def main():
    ap = argparse.ArgumentParser(description="절제 실험")
    ap.add_argument("--exp", default="ablation-2", help="실험 이름 — 같은 이름으로 다시 부르면 이어서 돈다")
    ap.add_argument("--questions", nargs="+", default=["Q2", "Q5", "Q8"])
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--settings", nargs="+", default=ABLATION2, choices=list(SETTINGS),
                    help="기본값은 ablation-2 의 8개 설정 — 기획보강끔은 ablation-3 에서만 쓴다")
    ap.add_argument("--summarize-only", action="store_true")
    ap.add_argument("--remeasure", action="store_true", help="저장된 실행의 지표를 지금 규칙으로 다시 잰다(LLM 없이)")
    args = ap.parse_args()
    if args.remeasure:
        n, changed = remeasure()
        print(f"지표를 다시 쟀다 — 실행 {n}건 중 {changed}건의 값이 바뀌었다")
        args.summarize_only = True
    settings = ["기본"] + [s for s in args.settings if s != "기본"]   # 기본이 먼저 — 대조군 예산의 기준
    if not args.summarize_only:
        total = len(args.questions) * args.repeats * len(settings)
        print(f"실험 {args.exp}: 질문 {args.questions} × 설정 {len(settings)}개 × {args.repeats}회 = {total}번 "
              f"(이미 끝난 것은 건너뛴다)", flush=True)
        with ThreadPoolExecutor(max_workers=len(args.questions)) as pool:   # 질문마다 한 줄씩 동시에
            list(pool.map(lambda q: run_question(args.exp, q, args.repeats, settings), args.questions))
    summary = summarize(args.exp)
    if args.exp in LEGACY:
        print(f"※ {LEGACY[args.exp]}")
    print_table(summary)
    print(f"\n저장: {graph._rel(out_path(args.exp))}")


if __name__ == "__main__":
    sys.exit(main())
