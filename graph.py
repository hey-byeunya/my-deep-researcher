#!/usr/bin/env python3
"""딥리서처 파이프라인 — 기획 → 배치 → 서브에이전트 → 점검 → 종합 → 측정.

  python graph.py --question Q5              # questions.json 의 Q5 로 한 번 돌린다
  python graph.py --question "자유 질문"      # 직접 쓴 질문으로
  python graph.py --question Q5 --plan-only  # 기획까지만 (목차·배정 확인용)

도메인 값은 config.json 에, 실험 스위치(역할·배정·구역·재위임)는 SWITCHES 에 둔다.
수업 코드(agent-team-agt1/deep_research_team.py)에서 두 가지를 바꿨다.
  · 비용 기록을 전역 변수가 아니라 State 의 cost 목록에 쌓는다 — 누가(코디네이터/서브에이전트)
    어느 절에서 몇 글자를 봤는지가 실행 결과에 그대로 남아, 병렬로 돌아도 격리를 숫자로 증명할 수 있다.
  · 예산 단위를 '문서 건수'에서 '읽은 글자 수'로 바꿨다 — 이 코퍼스는 문서 길이가 379자~5만 자로
    극단적이라, 건수 예산이면 처칠 한 건과 싱어 한 건이 같은 값이 된다.
"""
import argparse
import json
import operator
import os
import re
import sys
import time
from pathlib import Path
from typing import Annotated, TypedDict

from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

BASE = Path(__file__).parent
load_dotenv(BASE / ".env")

CONFIG = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
ROSTER = CONFIG["역할"]

# 실험 스위치 — ablation.py 가 하나씩 끈다
SWITCHES = {
    "역할": True,     # 끄면 모든 절이 기본역할 하나로 일한다
    "배정": True,     # 끄면 시작 문서를 주지 않는다 (서브에이전트가 알아서 고른다)
    "구역": True,     # 끄면 남의 구역(피하기 목록)을 알려 주지 않는다
    "담당구역": True, # 끄면 수업 방식 — 남의 구역을 '다른 절의 시작문서 + 이미 읽은 문서'로만 잡는다
    "재위임": True,   # 끄면 1바퀴로 끝낸다
}

# 목차가 형식 단위로 나뉘었는지 알아보는 표지 — 이런 절은 전원이 같은 자료를 읽게 만든다
FORMAT_WORDS = re.compile(r"^(개요|서론|배경|연표|요약|결론|맺음말|분석|평가|종합|의의|한계)$")


# ─── 코퍼스 ──────────────────────────────────────────────────────

def load_corpus(path=BASE / "data" / "corpus.json"):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data


CORPUS = load_corpus()
DOCS = CORPUS["docs"]
LINKS = CORPUS["links"]
KINDS = CORPUS.get("_종류", {})
AUTHOR_OF = CORPUS.get("_작가", {})
# 공용 서가 — 공통 문서(노벨 문학상·노벨상·공산주의 …)는 여러 절이 함께 읽어야 하므로
# 누구의 담당문서로도 묶지 않고 피하기 목록에도 넣지 않는다. 수업 코드의 교훈
# "핵심 문서를 피하기에 넣지 말 것(block_key_doc 실패)"을 구역 규칙으로 옮긴 것이다.
SHARED = frozenset(t for t, k in KINDS.items() if k == "공통")
# 수상 연도 — 노벨상 공식 API 에서 온 값 (check_seeds.py 가 data/award_years.json 에 남긴다)
_years_file = BASE / "data" / "award_years.json"
AWARD_YEARS = (json.loads(_years_file.read_text(encoding="utf-8"))["연도"]
               if _years_file.exists() else {})


# ─── 상태 ───────────────────────────────────────────────────────

class Research(TypedDict):
    question: str
    switches: dict
    plan: dict
    sections: Annotated[list, operator.add]   # 리듀서가 없으면 병렬 원고가 서로 덮어쓴다
    visited: Annotated[list, operator.add]    # [절, 문서, 읽은 글자] 기록
    cost: Annotated[list, operator.add]       # {"누가": 코디/서브, "절": …, "글자": …, "단계": …}
    log: Annotated[list, operator.add]
    report: str
    metrics: dict
    task: dict      # Send 로 서브에이전트 한 명에게만 가는 일감
    prior: dict     # 재위임 때 그 절의 지난 바퀴 원고


# ─── LLM 호출 ────────────────────────────────────────────────────

_llm = None
TRANSIENT = ("RateLimit", "APIConnection", "Timeout", "InternalServer")


def _invoke(messages):
    """실제 모델 호출. 테스트는 이 함수를 가짜로 바꿔 끼운다."""
    global _llm
    if _llm is None:
        from langchain_openai import ChatOpenAI
        if not os.getenv("OPENAI_API_KEY", "").startswith("sk-"):
            sys.exit("OPENAI_API_KEY 가 없다 — .env.example 을 .env 로 복사해 채운다")
        _llm = ChatOpenAI(model=CONFIG["모델"], temperature=CONFIG["온도"], timeout=90, max_retries=0)
    for attempt in range(4):
        try:
            return _llm.invoke(messages).content
        except Exception as e:
            if attempt == 3 or not any(k in type(e).__name__ for k in TRANSIENT):
                raise
            time.sleep(2 ** attempt)


def ask(system, user, who, section="", stage=""):
    """모델에 묻고, 본 글자 수를 비용 기록 한 줄로 함께 돌려준다.

    who 는 "코디" 또는 "서브". 시스템 프롬프트는 세지 않는다 — 격리를 볼 때 궁금한 것은
    '자료를 얼마나 봤나'이지 지시문 길이가 아니기 때문이다.
    """
    text = _invoke([{"role": "system", "content": system}, {"role": "user", "content": user}])
    return text, {"누가": who, "절": section, "단계": stage, "글자": len(user)}


def jload(raw, default):
    """모델 응답에서 JSON 덩어리를 꺼낸다. 깨졌으면 default."""
    try:
        m = re.search(r"\{.*\}" if isinstance(default, dict) else r"\[.*\]", raw, re.S)
        return json.loads(m.group(0))
    except Exception:
        return default


# ─── ① 기획 ─────────────────────────────────────────────────────

def cards(width=None):
    """코디네이터가 보는 문서 카드. 공용 서가 → 수상자(수상 연도순) 차례로, 작품은 작가 밑에 제목만.

    작품까지 카드를 만들면 176건 × (제목 + 120자)가 되어 코디네이터가 보는 양이 크게 늘고
    격리가 약해진다. 작품은 작가 문서가 가리키므로 제목만 알려 줘도 배정할 수 있다.
    수상자 카드에는 공식 수상 연도를 붙이고 연도순으로 늘어놓는다 — 앞 120자만으로는 언제
    받았는지 드러나지 않는 문서가 많아, 코디네이터가 시대로 나눌 근거가 없었다.
    """
    width = width or CONFIG["카드_글자"]
    works = {}
    for w, a in AUTHOR_OF.items():
        works.setdefault(a, []).append(w)

    def head(t):
        return re.sub(r"\s+", " ", DOCS[t][:width]).strip()

    shared = [t for t in DOCS if KINDS.get(t) == "공통"]
    laureates = sorted((t for t in DOCS if KINDS.get(t) == "수상자"),
                       key=lambda t: (AWARD_YEARS.get(t, ["9999"])[0], t))
    lines = ["[공용 서가 — 여러 절이 함께 쓸 수 있다]  형식: - 문서 제목 : 앞부분"]
    lines += [f"- {t}: {head(t)}" for t in shared]
    lines.append("\n[수상자 — 수상 연도순]  형식: - 문서 제목 ｜ 수상 연도 ｜ 앞부분  (시작문서·담당문서에는 '문서 제목'만 적는다)")
    for t in laureates:
        year = "·".join(AWARD_YEARS.get(t, [])) or "연도 미상"
        lines.append(f"- {t} ｜ {year}년 수상 ｜ {head(t)}")
        if t in works:
            lines.append(f"    · 작품 문서: {', '.join(works[t])}")
    return "\n".join(lines)


def resolve_title(name, docs):
    """모델이 적은 문서 제목을 코퍼스 제목으로 맞춘다. '한강' -> '한강 (작가)' 같은 것만 구제한다."""
    name = (name or "").strip().strip("«»《》『』「」\"'")
    # 카드에 붙인 수상 연도를 제목째 베껴 오는 경우 ("셀마 라겔뢰프 (1909년 수상)")
    name = re.sub(r"\s*[(｜|]\s*\d{4}(·\d{4})*년 수상.*$", "", name).strip()
    if name in docs:
        return name
    base = {re.sub(r"\s*\([^)]*\)$", "", t): t for t in docs}
    return base.get(name)


def allocate_budget(weights, total, min_ratio):
    """절마다 비중(1~3)을 받아 전체 예산을 나눈다. 가장 작은 절도 평균의 min_ratio 는 받는다."""
    if not weights:
        return []
    ws = [min(3, max(1, int(w))) if str(w).isdigit() else 2 for w in weights]
    mean = total / len(ws)
    raw = [total * w / sum(ws) for w in ws]
    floor = mean * min_ratio
    lifted = [max(r, floor) for r in raw]
    scale = total / sum(lifted)
    return [int(r * scale) for r in lifted]


def validate_plan(obj, docs, roster, cfg, switches, shared=SHARED):
    """모델이 짠 목차를 코드로 검사하고 고친다. (목차, 교정 기록) 을 돌려준다.

    모델은 그럴듯한 제목을 지어내므로, 시작 문서가 코퍼스에 실제로 있는지·절끼리 겹치지 않는지·
    역할이 명단에 있는지·절 제목이 형식 단위가 아닌지를 여기서 본다. 고칠 수 없는 시작 문서는
    엉뚱한 문서로 바꾸지 않고 비워 둔다(서브에이전트가 스스로 고른다) — 수업 코드는 코퍼스 첫 문서로
    채웠는데, 그러면 질문과 무관한 문서가 배정된다.
    """
    fixes, toc, taken = [], [], set()
    items = [i for i in obj.get("목차", []) if isinstance(i, dict)][:cfg["절수"]]
    total = cfg["절수"] * cfg["절예산_글자"]
    budgets = allocate_budget([i.get("비중", 2) for i in items], total, cfg["절예산_최소비율"])
    for idx, (item, budget) in enumerate(zip(items, budgets)):
        title = str(item.get("절") or f"절 {idx + 1}").strip()
        if FORMAT_WORDS.match(title):
            fixes.append(f"형식 단위 절 제목: «{title}»")
        role = item.get("역할", "")
        if not switches["역할"]:
            role = cfg["기본역할"]
        elif role not in roster:
            fixes.append(f"«{title}» 역할 '{role}' 이 명단에 없어 '{cfg['기본역할']}' 로 바꿈")
            role = cfg["기본역할"]
        seed, territory = "", []
        if switches["배정"]:
            asked = str(item.get("시작문서") or "")
            wanted = [asked] + [str(x) for x in (item.get("담당문서") or []) if str(x) != asked]
            for i, name in enumerate(wanted):
                found = resolve_title(name, docs)
                if found is None:
                    fixes.append(f"«{title}» {'시작' if i == 0 else '담당'}문서 '{name}' 가 코퍼스에 없어 뺌")
                elif found in taken:
                    fixes.append(f"«{title}» {'시작' if i == 0 else '담당'}문서 '{found}' 가 다른 절과 겹쳐 뺌 (구역 겹침)")
                elif found not in territory:
                    if found != name:
                        fixes.append(f"«{title}» '{name}' → '{found}' 로 맞춤")
                    territory.append(found)
                    if found not in shared:        # 공용 서가는 여러 절이 함께 쓴다
                        taken.add(found)
            territory = territory[:cfg.get("담당문서_상한", 6)]
            seed = territory[0] if territory else ""
            if territory and resolve_title(asked, docs) != seed:
                fixes.append(f"«{title}» 시작문서를 담당문서 첫 건 '{seed}' 로 대신함")
        toc.append({"절": title, "지시": str(item.get("지시") or "").strip(), "역할": role,
                    "시작문서": seed, "담당문서": territory, "예산": budget})
    return toc, fixes


PLAN_SYSTEM = """너는 리서치 팀의 코디네이터다. 질문에 답하는 장문 보고서의 목차를 짜고, 절마다 조사관 한 명을 붙인다.
절은 최대 {n}개.

- 목차는 반드시 **내용 단위**로 나눈다 — 작가·사례·시대·쟁점·작품처럼 질문이 실제로 묻는 덩어리로.
  '개요·배경·연표·분석·결론' 같은 형식 단위로 나누지 마라. 그러면 조사관 모두가 같은 문서를 읽는다.
- 절마다 그 절이 맡을 **담당문서** 2~6건을 문서 목록에서 제목 그대로 고르고, 그중 가장 먼저 읽을 것을
  **시작문서**로 적는다. 절끼리 담당문서가 겹치면 안 된다 — 겹치지 않게 나눌 수 없다면 목차를 잘못 짠 것이다.
  '작품 문서'로 적힌 제목도 고를 수 있다.
- 좋은 예 — 질문 "라틴아메리카 수상자들은 무엇을 썼나": 「미스트랄·네루다(칠레)」「아스투리아스(과테말라)」
  「가르시아 마르케스(콜롬비아)」「파스·바르가스 요사」처럼 사람·나라로 나누고 절마다 그 문서를 담당시킨다.
  나쁜 예 — 「역사적 흐름」「주요 주제」「사회적 맥락」: 세 절 모두 같은 수상자 문서를 읽어야 한다.
- 절마다 **비중**(1~3)을 매긴다. 읽을 자료가 많고 넓은 절일수록 크게. 전체 읽기 예산은 이 비율로 나뉜다.
- 역할은 명단에 있는 이름을 그대로 쓴다.
- 질문이 문서 한두 건으로 답이 나오는 것이라면 절을 적게(1~2개) 짜도 된다.

[조사관 명단]
{roster}

JSON 으로만 답한다:
{{"제목": "보고서 제목", "목차": [{{"절": "절 제목", "지시": "이 절에서 밝힐 것 한두 문장", "역할": "명단 이름", "시작문서": "문서 제목", "담당문서": ["문서 제목", "..."], "비중": 2}}]}}"""


def plan_problems(toc, fixes, switches):
    """다시 짜게 할 만한 문제 — 구역 겹침 2건 이상, 또는 배정이 켜졌는데 담당문서가 빈 절.

    담당문서를 겹치지 않게 나눌 수 없다는 것은 대개 목차가 형식 단위(생애·작품·배경·비교)로
    짜였다는 신호다. 같은 질문도 시행마다 목차가 달라지므로, 한 번 더 기회를 준다.
    """
    problems = [f for f in fixes if "구역 겹침" in f or "형식 단위" in f]
    if switches["배정"]:
        problems += [f"«{t['절']}» 담당문서가 하나도 남지 않음" for t in toc if not t["담당문서"]]
    overlap = sum("구역 겹침" in f for f in fixes)
    return problems if (overlap >= 2 or any("남지 않음" in p for p in problems)
                        or any("형식 단위" in p for p in problems)) else []


def plan(s):
    sw = s["switches"]
    roster = "\n".join(f"- {k}: {v}" for k, v in ROSTER.items())
    system = PLAN_SYSTEM.format(n=CONFIG["절수"], roster=roster)
    user = f"[질문] {s['question']}\n\n[읽을 수 있는 문서]\n{cards()}"
    raw, c = ask(system, user, "코디", stage="기획")
    costs, log = [c], []
    obj = jload(raw, {})
    toc, fixes = validate_plan(obj, DOCS, ROSTER, CONFIG, sw)
    problems = plan_problems(toc, fixes, sw)
    if problems:
        # 한 번만 다시 짜게 한다. 코디네이터는 문서 카드를 다시 받으므로 그 글자도 코디 비용에 든다.
        log.append(f"① 기획   목차를 다시 짜게 함 — 문제 {len(problems)}건: " + " / ".join(problems[:3]))
        feedback = ("[직전 목차의 문제 — 코드가 검사했다]\n" + "\n".join(f"- {p}" for p in problems) +
                    "\n절끼리 담당문서가 겹치지 않도록 내용 단위(사람·나라·시대·사례·작품)로 다시 나눠라.")
        raw2, c2 = ask(system, user + "\n\n" + feedback, "코디", stage="기획(재)")
        costs.append(c2)
        obj2 = jload(raw2, {})
        toc2, fixes2 = validate_plan(obj2, DOCS, ROSTER, CONFIG, sw)
        if toc2 and len(plan_problems(toc2, fixes2, sw)) < len(problems):
            obj, toc, fixes = obj2, toc2, ["(재기획 채택) 첫 목차 문제: " + " / ".join(problems)] + fixes2
        else:
            fixes = fixes + ["(재기획했지만 나아지지 않아 첫 목차 유지)"]
    if not toc:
        fixes.append("목차를 읽지 못해 질문 하나짜리 절로 대신함")
        toc = [{"절": "질문 전체", "지시": s["question"], "역할": CONFIG["기본역할"], "시작문서": "",
                "담당문서": [], "예산": CONFIG["절수"] * CONFIG["절예산_글자"]}]
    p = {"제목": obj.get("제목") or s["question"], "목차": toc, "교정": fixes,
         "배치": list(range(len(toc))), "바퀴": 1}
    seeds = " · ".join(f"{t['역할']}→«{t['시작문서'] or '자율'}»+{max(len(t.get('담당문서', [])) - 1, 0)}건"
                       f"({t['예산']:,}자)" for t in toc)
    log += [f"① 기획   목차 {len(toc)}절 · {seeds}"] + [f"   ⚠ 교정: {f}" for f in fixes]
    return {"plan": p, "cost": costs, "log": log}


# ─── ② 배치 ─────────────────────────────────────────────────────

def dispatch(s):
    n, w = len(s["plan"]["배치"]), s["plan"]["바퀴"]
    return {"log": [f"② 배치   {w}바퀴 · 서브에이전트 {n}명 동시 파견"]}


def zones(plan_toc, done, mine_index, switches, shared=SHARED):
    """이 절이 피해야 할 문서 — 다른 절의 구역 + 다른 절이 이미 읽은 문서. 공용 서가는 뺀다.

    담당구역 스위치를 끄면 수업 방식이 된다: 다른 절의 '시작문서'만 구역으로 본다.
    """
    if not switches["구역"]:
        return []
    mine = plan_toc[mine_index]["절"]
    avoid = set()
    for j, t in enumerate(plan_toc):
        if j == mine_index:
            continue
        if t.get("시작문서"):
            avoid.add(t["시작문서"])
        if switches.get("담당구역", True):
            avoid |= set(t.get("담당문서", []))
    avoid |= {d for name, sec in done.items() if name != mine for d in sec.get("읽은문서", [])}
    return sorted(avoid - shared)


def latest_sections(sections):
    """절 제목별로 가장 최근에 채택된 원고."""
    out = {}
    for sec in sections:
        if sec.get("채택", True):
            out[sec["절"]] = sec
    return out


def fanout(s):
    """배치 목록의 절마다 Send 하나. 비어 있으면 점검으로 바로 간다."""
    idxs = s["plan"]["배치"]
    if not idxs:
        return "review"
    toc, done = s["plan"]["목차"], latest_sections(s["sections"])
    return [Send("researcher", {
        "question": s["question"], "switches": s["switches"],
        "task": {**toc[i], "번호": i, "바퀴": s["plan"]["바퀴"],
                 "피하기": zones(toc, done, i, s["switches"])},
        "prior": done.get(toc[i]["절"], {})}) for i in idxs]


# ─── ③ 서브에이전트 · ④ 점검 · ⑤ 종합 — S4·S5 에서 채운다 ────────────────

def researcher(s):
    t = s["task"]
    return {"sections": [{"절": t["절"], "번호": t["번호"], "역할": t["역할"], "시작문서": t["시작문서"],
                          "예산": t["예산"], "피하기": t["피하기"], "바퀴": t["바퀴"],
                          "읽은문서": [], "본문": "(빈 노드 — S4 에서 채운다)", "충분": True}],
            "log": [f"③ 조사   «{t['절']}» (빈 노드)"]}


def review(s):
    return {"plan": {**s["plan"], "배치": []}, "log": ["④ 점검   (빈 노드)"]}


def route(s):
    return "more" if s["plan"]["배치"] else "done"


def synthesize(s):
    return {"report": "(빈 노드 — S5 에서 채운다)", "log": ["⑤ 종합   (빈 노드)"]}


def evaluate(s):
    return {"metrics": {}, "log": ["⑥ 측정   (빈 노드)"]}


# ─── 그래프 ─────────────────────────────────────────────────────

def build(plan_only=False):
    g = StateGraph(Research)
    g.add_node("plan", plan)
    if plan_only:
        g.add_edge(START, "plan")
        g.add_edge("plan", END)
        return g.compile()
    for name, fn in [("dispatch", dispatch), ("researcher", researcher), ("review", review),
                     ("synthesize", synthesize), ("evaluate", evaluate)]:
        g.add_node(name, fn)
    g.add_edge(START, "plan")
    g.add_edge("plan", "dispatch")
    g.add_conditional_edges("dispatch", fanout, ["researcher", "review"])
    g.add_edge("researcher", "review")
    g.add_conditional_edges("review", route, {"more": "dispatch", "done": "synthesize"})
    g.add_edge("synthesize", "evaluate")
    g.add_edge("evaluate", END)
    return g.compile()


def load_question(q):
    """'Q5' 처럼 주면 questions.json 에서 찾고, 아니면 그대로 질문으로 쓴다."""
    qs = json.loads((BASE / "data" / "questions.json").read_text(encoding="utf-8"))["questions"]
    hit = next((x for x in qs if x["id"].lower() == q.lower()), None)
    return (hit["id"], hit["질문"]) if hit else ("자유", q)


def run(question, plan_only=False, **switches):
    sw = {**SWITCHES, **switches}
    init = {"question": question, "switches": sw, "plan": {}, "sections": [], "visited": [],
            "cost": [], "log": [], "report": "", "metrics": {}, "task": {}, "prior": {}}
    return build(plan_only).invoke(init, {"recursion_limit": 60})


def main():
    ap = argparse.ArgumentParser(description="노벨 문학상 딥리서처")
    ap.add_argument("--question", default="Q5", help="questions.json 의 id 또는 질문 문장")
    ap.add_argument("--plan-only", action="store_true", help="기획까지만 돌리고 목차를 보여 준다")
    for name in SWITCHES:
        ap.add_argument(f"--no-{name}", action="store_true", help=f"'{name}' 스위치를 끈다")
    args = ap.parse_args()
    qid, question = load_question(args.question)
    off = {name: False for name in SWITCHES if getattr(args, f"no_{name}")}
    out = run(question, plan_only=args.plan_only, **off)
    print(f"[{qid}] {question}\n")
    print("\n".join(out["log"]))
    if args.plan_only:
        print(json.dumps(out["plan"], ensure_ascii=False, indent=1))
    coord = sum(c["글자"] for c in out["cost"] if c["누가"] == "코디")
    print(f"\n코디네이터가 본 글자 {coord:,} · 코퍼스 전체 {sum(map(len, DOCS.values())):,}")


if __name__ == "__main__":
    main()
