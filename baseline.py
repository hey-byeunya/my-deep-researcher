#!/usr/bin/env python3
"""혼자 하는 대조군 — 나누지 않는다. 한 에이전트가 고르고, 읽고, 혼자 다 쓴다.

  python baseline.py --question Q5            # 같은 질문의 가장 최근 팀 실행(기본 설정)과 짝을 짓는다
  python baseline.py --question Q5 --pair ID  # 특정 팀 실행과 짝을 짓는다

공정하게 하려고 팀과 같은 것을 준다.
  · 같은 자료(corpus.json) · 같은 모델(config.json) · 같은 도구 — 문서 카드, 링크 후보, 한 번에 2,500자씩
    읽고 간추리기(graph.read_chunk), 근거를 데이터로 받는 집필
  · 같은 읽기 예산 — 짝지은 팀 실행이 실제로 읽은 글자 수(재위임 포함)를 그대로 받는다
  · 같은 모양의 결과 — 팀 목차와 같은 절 수로 쓰게 한다
예산을 다 쓰는지 확인한다. 중간에 멈추면 이긴 것이 아니라 상대를 묶어 둔 것이기 때문이다. 모델이 그만
읽겠다고 하면 예산이 남았다고 알리고 한 번 더 묻고, 그래도 못 고르면 질문 단어와 가장 많이 겹치는 후보를
읽힌다. 수업 코드처럼 후보 첫 번째를 읽히면 가나다순으로 무관한 문서(Q5 에서 가르시아 마르케스·가오싱젠·
귄터 그라스)에 예산이 새서, 예산은 같아도 실제로는 손발을 묶은 비교가 된다.

다른 점은 딱 하나, 나누지 않는다는 것이다. 모은 요약이 모두 한 창에 쌓이고, 보고서 전체를 한 번에 쓴다.
그래서 이 에이전트가 본 글자는 모두 '코디'(한 사람이 전부 본 것)로 센다 — 격리율 100%.
"""
import argparse
import json
import re
import sys

import graph
import metrics

SOLO = {"절": "(혼자)", "번호": 0, "바퀴": 1, "역할": "단독 연구자",
        "역할설명": "질문 하나를 혼자 맡아 자료를 고르고 읽고 보고서 전체를 쓴다", "피하기": []}

START_SYSTEM = """너는 질문 하나를 혼자 맡은 연구자다. 문서 목록을 보고, 먼저 읽을 문서를 중요한 순서대로 고른다.
JSON 으로만: {"읽을문서": ["문서 제목", ...]}  (제목은 목록에 적힌 그대로, 최대 12건)"""

PICK_SYSTEM = """너는 질문 하나를 혼자 맡은 연구자다. 지금까지 모은 요약을 보고, 다음에 읽을 문서를 후보에서 정확히 하나 고른다.
JSON 으로만: {"문서": "후보 제목 그대로"}"""

WRITE_SYSTEM = """너는 조사 결과를 혼자 정리하는 필자다. 아래 요약만 근거로 질문에 답하는 보고서를 쓴다.
- 절은 {n}개. 절마다 여섯 문장 이상.
- 문장마다 근거가 된 문서를 '근거' 칸에 적는다. [근거로 쓸 수 있는 문서] 에 있는 제목만 그대로 쓴다.
- 작품·책 이름은 문장 안에서 《》 로 쓴다. 요약에 없는 내용은 쓰지 않는다.
- 머리말과 맺음말은 각각 세 문장 이내, 새 사실을 넣지 않는다.
JSON 으로만:
{{"제목": "...", "머리말": "...", "절": [{{"제목": "절 제목", "문장": [{{"글": "문장", "근거": ["문서 제목"]}}]}}], "맺음말": "..."}}"""


def pair_run(qid, pair_id=None, path=graph.OUTPUT / "runs.jsonl"):
    """짝지을 팀 실행 — 지정했으면 그것, 아니면 같은 질문의 가장 최근 '팀·기본' 실행."""
    if not path.exists():
        sys.exit("output/runs.jsonl 이 없다 — 먼저 python graph.py --question " + qid)
    runs = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if pair_id:
        hit = [r for r in runs if r["id"] == pair_id]
    else:
        hit = [r for r in runs if r["질문id"] == qid and r["종류"] == "팀" and r["설정"] == "기본"]
    if not hit:
        sys.exit(f"짝지을 팀 실행이 없다 ({qid}) — 먼저 python graph.py --question {qid}")
    return hit[-1]


def closest(question, pool):
    """질문 단어(2글자 이상)가 문서 앞 300자에 가장 많이 나오는 후보. 모델을 부르지 않는 대체 규칙."""
    words = {w for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", question)}

    def score(d):
        head = graph.DOCS[d][:300]
        return sum(w in head for w in words)

    return max(pool, key=lambda d: (score(d), -pool.index(d)))


def solo(question, budget, n_sections):
    t = {**SOLO, "지시": question, "예산": budget}
    costs, visits, notes, read_pos = [], [], [], {}
    log = [f"◎ 혼자   예산 {budget:,}자 (짝 팀 실행이 실제로 읽은 양) · 절 {n_sections}개로 쓴다"]

    # ① 팀 코디네이터와 같은 문서 카드를 보고 먼저 읽을 목록을 고른다
    raw, c = graph.ask(START_SYSTEM, f"[질문] {question}\n\n[읽을 수 있는 문서]\n{graph.cards()}",
                       "코디", "(혼자)", "기획")
    costs.append(c)
    queue = [d for d in (graph.resolve_title(str(x), graph.DOCS)
                         for x in graph.jload(raw, {}).get("읽을문서", [])) if d]
    queue = list(dict.fromkeys(queue))

    # ② 예산이 다할 때까지 — 목록 → 모델이 고른 후보. 멈추거나 엉뚱하면 후보 첫 번째 (예산은 끝까지 쓴다)
    spent, forced = 0, 0
    cap = graph.CONFIG["문서당_읽기상한"]
    while budget - spent >= graph.MIN_READ:
        doc = next((d for d in queue if d not in read_pos), None)
        if doc is None:
            cand, unfinished = graph.candidates(read_pos, set())
            pool = cand[:80] + unfinished
            if not pool:
                break
            memo = "\n".join(f"- {d}: {n[:120]}" for d, n in notes[-30:]) or "(없음)"
            lines = [f"- {graph.label(d)}" for d in cand[:80]]
            lines += [f"- {d} (이어 읽기: {read_pos[d]:,}/{len(graph.DOCS[d]):,}자 읽음)" for d in unfinished]
            user = f"[질문] {question}\n[지금까지 모은 요약]\n{memo}\n[후보]\n" + "\n".join(lines)
            raw, c = graph.ask(PICK_SYSTEM, user, "코디", "(혼자)", "다음문서")
            costs.append(c)
            doc = graph.resolve_title(str(graph.jload(raw, {}).get("문서") or ""), graph.DOCS)
            if doc not in pool:
                # 그만두려 하거나 엉뚱한 제목이면 한 번 더 묻는다 — 예산이 남았음을 알려 준다
                raw, c = graph.ask(PICK_SYSTEM, user + f"\n\n[알림] 읽기 예산이 {budget - spent:,}자 남았다. "
                                   "질문과 관련 있을 만한 문서를 후보에서 하나 골라라.", "코디", "(혼자)", "다음문서(재)")
                costs.append(c)
                doc = graph.resolve_title(str(graph.jload(raw, {}).get("문서") or ""), graph.DOCS)
            if doc not in pool:
                # 그래도 못 고르면 질문 단어와 가장 많이 겹치는 후보 — 가나다순 첫 번째를 기계적으로
                # 읽히면(수업 코드) 무관한 문서에 예산이 새서 대조군 손발을 묶게 된다
                doc, forced = closest(question, pool), forced + 1
        start = read_pos.get(doc, 0)
        summary, n = graph.read_chunk(t, doc, start, min(cap, budget - spent), costs)
        costs[-1]["누가"] = "코디"                       # 혼자 하는 쪽은 전부 한 사람이 본다
        read_pos[doc] = start + n
        spent += n
        visits.append(["(혼자)", doc, n, 1])
        if not summary.startswith("관련 없음"):
            notes.append([doc, summary])
    log.append(f"   ◎ {len(visits)}회 읽기 · {spent:,}/{budget:,}자 · 문서 {len(read_pos)}건"
               + (f" · 끝내 못 골라 질문 단어로 대신 고른 횟수 {forced}" if forced else ""))

    # ③ 모은 요약을 한 창에 모두 넣고 보고서 전체를 한 번에 쓴다
    material = "\n\n".join(f"[자료: {d}]\n{n}" for d, n in notes) or "(읽은 자료 없음)"
    allowed = ", ".join(dict.fromkeys(d for d, _ in notes)) or "(없음)"
    raw, c = graph.ask(WRITE_SYSTEM.format(n=n_sections),
                       f"[질문] {question}\n[근거로 쓸 수 있는 문서] {allowed}\n\n[모은 요약]\n{material}",
                       "코디", "(혼자)", "집필")
    costs.append(c)
    obj = graph.jload(raw, {})
    sections, parts = [], [f"# {obj.get('제목') or question}", str(obj.get("머리말") or "").strip()]
    for i, part in enumerate(obj.get("절") or []):
        body = graph.render(part.get("문장") or [])
        text, removed = graph.strip_invalid(body, set(read_pos))   # 팀과 같은 인용 검사 규칙
        title = str(part.get("제목") or f"절 {i + 1}")
        sections.append({"절": title, "번호": i, "바퀴": 1, "역할": SOLO["역할"], "본문": text,
                         "예산": budget if i == 0 else 0, "읽은문서": list(read_pos),
                         "제거된인용": removed, **graph.check_citations(text, set(read_pos))})
        parts.append(f"## {i + 1}. {title}\n\n{text}")
    parts.append(f"## 맺음말\n\n{str(obj.get('맺음말') or '').strip()}")
    report = "\n\n".join(p for p in parts if p.strip())
    return {"question": question, "switches": {"혼자": True},
            "plan": {"제목": obj.get("제목") or question, "목차": [{"절": s["절"]} for s in sections],
                     "교정": [], "채택": {s["절"]: 1 for s in sections}},
            "sections": sections, "visited": visits, "cost": costs, "report": report, "log": log,
            "메모": notes, "강제읽기": forced}


def main():
    ap = argparse.ArgumentParser(description="혼자 하는 대조군")
    ap.add_argument("--question", default="Q5")
    ap.add_argument("--pair", help="짝지을 팀 실행 id (없으면 같은 질문의 가장 최근 팀·기본 실행)")
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args()
    qid, question = graph.load_question(args.question)
    team = pair_run(qid, args.pair)
    budget = sum(v[2] for v in team["visited"])
    out = solo(question, budget, len(team["sections"]))
    m, detail = metrics.measure(graph.record_of(out), graph.CORPUS)
    out["metrics"] = {**m, "_세부": detail, "_짝": team["id"]}
    print(f"[{qid}] {question}\n짝 팀 실행: {team['id']}\n")
    print("\n".join(out["log"]))
    print(f"⑥ 측정   {metrics.one_line(m)}")
    if m["예산소진율"] < 95:
        print(f"⚠ 예산을 {m['예산소진율']}% 만 썼다 — 대조군이 중간에 멈췄다. 공정한 비교가 아니다.")
    tm = team["metrics"]
    print(f"\n팀 대 혼자 — 근거율 {tm['근거율']} : {m['근거율']} · 읽은문서 {tm['읽은문서']} : {m['읽은문서']} · "
          f"편중 {tm['인용편중']} : {m['인용편중']} · 보고서 {tm['보고서자수']:,} : {m['보고서자수']:,}자 · "
          f"LLM {tm['LLM호출']} : {m['LLM호출']}회")
    if not args.no_save:
        run_id, path = graph.save_run(out, qid, label="짝-" + team["id"].split("_")[0], kind="혼자")
        print(f"\n저장: {graph._rel(path)} · output/runs.jsonl ({run_id})")


if __name__ == "__main__":
    main()
