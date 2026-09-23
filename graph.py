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
import hashlib
import json
import operator
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Annotated, TypedDict

from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

import metrics

BASE = Path(__file__).parent
load_dotenv(BASE / ".env")

CONFIG = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
ROSTER = CONFIG["역할"]

# 실험 스위치 — ablation.py 가 하나씩 끈다
SWITCHES = {
    "역할": True,     # 끄면 모든 절이 기본역할 하나로 일한다
    "배정": True,     # 끄면 시작 문서를 주지 않는다 (서브에이전트가 알아서 고른다)
    "구역": True,     # 끄면 남의 구역(피하기 목록)을 알려 주지 않는다
    "담당구역": False, # 켜면 남의 '담당문서'까지 피하기에 넣는다. 끄면 수업 방식 — 남의 시작문서 + 이미 읽은 문서.
                      # S7 절제 실험(ablation-1)에서 세 질문 모두 차이가 표준편차 안이어서 단순한 수업 방식으로
                      # 확정했다. 담당문서는 읽는 목록(배정)으로는 계속 쓴다 — 효과는 그쪽에서 나왔다.
    "재위임": True,   # 끄면 1바퀴로 끝낸다
    "재촉": False,    # 켜면 서브에이전트가 그만 읽겠다고 할 때 남은 예산을 알리고 한 번 더 묻고, 그래도 못 고르면
                      # 지시문 단어와 가장 많이 겹치는 후보를 읽힌다(대조군과 같은 규칙). 배정끔 해석 보강용.
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
_llm_lock = threading.Lock()   # 서브에이전트들이 동시에 첫 호출을 해도 모델 객체는 하나만 만든다
TRANSIENT = ("RateLimit", "APIConnection", "Timeout", "InternalServer")


def _invoke(messages):
    """실제 모델 호출. 테스트는 이 함수를 가짜로 바꿔 끼운다."""
    global _llm
    with _llm_lock:
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
        m = re.search(r"\[.*\]" if isinstance(default, list) else r"\{.*\}", raw, re.S)
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
    if sw["배정"] and any(not t["담당문서"] for t in toc) and any(t["담당문서"] for t in toc):
        # 재기획 뒤에도 구역이 빈 절(대개 '비교·종합' 같은 형식 절)은 파견하지 않는다. 격리된 서브에이전트는
        # 남의 절 자료를 볼 수 없어서 그런 절은 쓸 재료가 없다. 그 예산은 남은 절에 비율대로 나눠 준다.
        empty = [t["절"] for t in toc if not t["담당문서"]]
        total = sum(t["예산"] for t in toc)
        toc = [t for t in toc if t["담당문서"]]
        kept = sum(t["예산"] for t in toc)
        for t in toc:
            t["예산"] = int(t["예산"] * total / kept)
        fixes.append(f"구역이 빈 절 {len(empty)}개는 파견하지 않고 예산을 나눔: «{'», «'.join(empty)}»")
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
    """이 절이 피해야 할 문서 — 다른 절의 시작문서 + 다른 절이 이미 읽은 문서. 공용 서가는 뺀다.

    담당구역 스위치를 켜면 다른 절의 담당문서 전부도 피한다. 기본은 끔(수업 방식, S7 에서 확정).
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
        if switches.get("담당구역", False):
            avoid |= set(t.get("담당문서", []))
    avoid |= {d for name, sec in done.items() if name != mine for d in sec.get("읽은문서", [])}
    return sorted(avoid - shared)


def chosen_sections(sections, adopted=None):
    """절 제목별로 채택된 원고. adopted({절: 바퀴})가 없으면 가장 최근 원고."""
    out = {}
    for sec in sections:
        name = sec["절"]
        if adopted and name in adopted:
            if sec["바퀴"] == adopted[name]:
                out[name] = sec
        else:
            out[name] = sec
    return out


def fanout(s):
    """배치 목록의 절마다 Send 하나. 비어 있으면 점검으로 바로 간다."""
    idxs = s["plan"]["배치"]
    if not idxs:
        return "review"
    toc = s["plan"]["목차"]
    done = chosen_sections(s["sections"], s["plan"].get("채택"))
    return [Send("researcher", {
        "question": s["question"], "switches": s["switches"],
        "task": {**toc[i], "번호": i, "바퀴": s["plan"]["바퀴"],
                 "피하기": zones(toc, done, i, s["switches"])},
        "prior": done.get(toc[i]["절"], {})}) for i in idxs]


# ─── ③ 서브에이전트 ─────────────────────────────────────────────
# 서브에이전트는 자기 절 하나만 안다. 코디네이터가 준 역할·지시·시작문서·담당문서·예산·피하기
# 목록과, 재위임이면 지난 바퀴의 자기 원고뿐이다. 읽은 원문은 여기(메모)에 남고 위로는 원고만 올라간다.

MIN_READ = 300   # 남은 예산이 이보다 적으면 더 읽지 않는다 (앞부분 몇 줄만 읽고 판단하는 일을 막는다)


def role_line(t):
    desc = t.get("역할설명") or ROSTER.get(t["역할"], ROSTER[CONFIG["기본역할"]])
    return f"너는 {t['역할']}이다. {desc}."


def candidates(read_pos, avoid, docs=None, links=None):
    """다음에 읽을 후보. ① 읽은 문서가 가리키는 문서 ② 덜 읽은 문서(이어 읽기) ③ 없으면 나머지 전부.

    어느 경우에도 피하기 목록(남의 구역)은 풀지 않는다. 수업 코드는 ③ 까지 비면(구역 밖 문서를 전부
    읽으면) 마지막으로 구역을 풀었는데, 그러면 다른 절과 같은 문서를 읽게 된다. 이 코퍼스에서는 한 절이
    한 바퀴에 3~5건을 읽고 구역 밖에 160건쯤 남아 그 단계에 닿지 않으므로 실제 차이는 거의 없다.
    ③ 에서 수상자는 수상 연도순으로, 연도를 붙여 준다. 전부 보여 준다(176건, 약 3천 자) — 처음엔 앞 80건만
    보여 줬는데, 그러면 1990년대 이후 수상자(모리슨·한강 …)가 후보에 아예 나오지 않았다(S7 테스트에서 발견).
    """
    docs, links = docs or DOCS, links or LINKS
    blocked = set(avoid)
    unfinished = [d for d, pos in read_pos.items() if pos < len(docs[d])]
    frontier = sorted({x for d in read_pos for x in links.get(d, []) if x in docs}
                      - set(read_pos) - blocked)
    if frontier or unfinished:
        return frontier, unfinished
    rest = [d for d in docs if d not in read_pos and d not in blocked]
    rest.sort(key=lambda d: (KINDS.get(d) != "수상자", AWARD_YEARS.get(d, ["9999"])[0], d))
    return rest, []


def label(d):
    year = AWARD_YEARS.get(d)
    return f"{d} ({'·'.join(year)}년 수상)" if year else d


def closest(text, pool):
    """text 의 단어(2글자 이상)가 문서 앞 300자에 가장 많이 나오는 후보. 모델을 부르지 않는 대체 규칙."""
    words = set(re.findall(r"[가-힣A-Za-z0-9]{2,}", text))
    return max(pool, key=lambda d: (sum(w in DOCS[d][:300] for w in words), -pool.index(d)))


def pick_next(t, read_pos, avoid, costs, remaining=0, nudge=False):
    """링크 후보 중 하나를 모델이 고른다. 더 읽을 것이 없으면 None.

    nudge(재촉 스위치)가 켜져 있으면 그만두려 할 때 남은 예산을 알리고 한 번 더 묻고, 그래도 못 고르면
    지시문 단어와 가장 많이 겹치는 후보를 고른다 — 혼자 하는 대조군(baseline.py)과 같은 규칙이다.
    """
    cand, unfinished = candidates(read_pos, avoid)
    if not cand and not unfinished:
        return None
    pool = cand + unfinished
    lines = [f"- {label(d)}" for d in cand]
    lines += [f"- {d} (이어 읽기: {read_pos[d]:,}/{len(DOCS[d]):,}자 읽음)" for d in unfinished]
    system = (f"{role_line(t)} 맡은 절을 쓰려고 다음에 읽을 문서를 후보에서 정확히 하나 고른다. "
              "후보에 쓸 만한 것이 없으면 그만둔다.\n"
              'JSON 으로만: {"문서": "후보 제목 그대로"} 또는 {"문서": null}')
    user = (f"[맡은 절] {t['절']}\n[지시] {t['지시']}\n"
            f"[이미 읽음] {', '.join(read_pos) or '없음'}\n[후보]\n" + "\n".join(lines))
    raw, c = ask(system, user, "서브", t["절"], "다음문서")
    costs.append(c)
    pick = resolve_title(str(jload(raw, {}).get("문서") or ""), DOCS)
    if pick in pool:
        return pick
    if not nudge:
        return None
    raw, c = ask(system, user + f"\n\n[알림] 읽기 예산이 {remaining:,}자 남았다. 맡은 절과 관련 있을 만한 "
                 "문서를 후보에서 하나 골라라.", "서브", t["절"], "다음문서(재)")
    costs.append(c)
    pick = resolve_title(str(jload(raw, {}).get("문서") or ""), DOCS)
    return pick if pick in pool else closest(f"{t['절']} {t['지시']}", pool)


def read_chunk(t, doc, start, size, costs):
    """문서의 [start, start+size) 구간을 읽고 지시에 관련된 것만 간추린다."""
    chunk = DOCS[doc][start:start + size]
    part = "" if start == 0 and start + size >= len(DOCS[doc]) else f" · {start:,}~{start + len(chunk):,}자 구간"
    raw, c = ask(f"{role_line(t)} 아래 문서 조각을 읽고 지시에 관련된 사실만 여섯 문장 이내로 간추린다. "
                 "사람·작품·연도·사건 이름은 문서에 적힌 그대로 옮긴다. 관련이 없으면 '관련 없음'이라고만 답한다.\n"
                 f"[지시] {t['지시']}",
                 f"[문서: {doc}{part}]\n{chunk}", "서브", t["절"], "읽기")
    costs.append(c)
    return raw.strip(), len(chunk)


def explore(t, prior, nudge=False):
    """예산(글자)만큼 읽는다. 순서: 시작문서 → 담당문서 → 모델이 고른 후보(링크 · 이어 읽기)."""
    read_pos = dict(prior.get("읽은위치", {}))
    notes = [list(n) for n in prior.get("메모", [])]
    avoid = set(t.get("피하기") or [])
    budget, spent, costs, visits = t["예산"], 0, [], []
    queue = [d for d in [t.get("시작문서")] + list(t.get("담당문서") or []) if d and d in DOCS]
    queue = list(dict.fromkeys(queue))
    cap = CONFIG["문서당_읽기상한"]
    while budget - spent >= MIN_READ:
        # 배정받은 문서는 한 조각씩 먼저 다 훑고(아직 안 읽은 것부터), 그다음에 이어 읽는다.
        # 긴 문서 하나를 끝까지 파느라 나머지 담당문서를 못 읽는 일을 막는다.
        doc = next((d for d in queue if d not in read_pos), None)
        doc = doc or next((d for d in queue if read_pos[d] < len(DOCS[d])), None)
        if doc is None:
            doc = pick_next(t, read_pos, avoid, costs, budget - spent, nudge)
            if doc is None:
                break
        start = read_pos.get(doc, 0)
        summary, n = read_chunk(t, doc, start, min(cap, budget - spent), costs)
        read_pos[doc] = start + n
        spent += n
        visits.append([t["절"], doc, n, t["바퀴"]])
        if not summary.startswith("관련 없음"):
            notes.append([doc, summary])
    return read_pos, notes, spent, costs, visits


WRITE_SYSTEM = """{role} 아래 자료만 근거로 보고서의 한 절을 쓴다.
- 여섯 문장 이상 열두 문장 이하, 합쳐서 600자 이상.
- 문장마다 그 문장의 근거가 된 문서를 '근거' 칸에 적는다. [근거로 쓸 수 있는 문서] 에 있는 제목만
  그대로 쓴다. 근거가 없는 문장은 쓰지 않는다.
- 작품·책 이름은 문장 안에서 《》 로 쓴다 (예: 《채식주의자》).
- 자료에 없는 내용은 쓰지 않는다.
- 지시를 자료로 다 채우지 못했으면 충분을 false 로 하고, 무엇이 비었는지 부족에 한 문장으로 적는다.
JSON 으로만:
{{"문장": [{{"글": "문장 하나", "근거": ["문서 제목"]}}], "충분": true, "부족": ""}}"""


def render(sentences_):
    """문장 목록을 본문으로 — 근거는 코드가 «제목» 으로 문장 끝(마침표 앞)에 붙인다."""
    out = []
    for item in sentences_:
        if not isinstance(item, dict):
            continue
        text = str(item.get("글") or "").strip()
        refs = [str(r).strip() for r in (item.get("근거") or []) if str(r).strip()]
        if not text:
            continue
        end = text[-1] if text[-1] in ".!?" else "."
        body = text[:-1] if text[-1] in ".!?" else text
        out.append(f"{body} «{', '.join(dict.fromkeys(refs))}»{end}" if refs else f"{body}{end}")
    return " ".join(out)


def write(t, notes, costs, prior_text="", feedback=""):
    material = "\n\n".join(f"[자료: {d}]\n{n}" for d, n in notes) or "(읽은 자료 없음)"
    allowed = ", ".join(dict.fromkeys(d for d, _ in notes)) or "(없음)"
    before = f"\n\n[지난 바퀴에 쓴 원고 — 고쳐 쓰되 맞는 내용은 살린다]\n{prior_text}" if prior_text else ""
    fix = f"\n\n[고칠 점]\n{feedback}" if feedback else ""
    raw, c = ask(WRITE_SYSTEM.format(role=role_line(t)),
                 f"[맡은 절] {t['절']}\n[지시] {t['지시']}\n[근거로 쓸 수 있는 문서] {allowed}\n\n"
                 f"[자료]\n{material}{before}{fix}",
                 "서브", t["절"], "집필(재)" if feedback else "집필")
    costs.append(c)
    obj = jload(raw, None)
    if isinstance(obj, dict) and isinstance(obj.get("문장"), list):
        text = render(obj["문장"])                  # 근거를 데이터로 받아 코드가 «» 를 붙인다
    elif isinstance(obj, dict) and "본문" in obj:   # 예전 형식도 받는다
        text = str(obj.get("본문") or "").strip()
    else:                         # JSON 자체가 깨졌을 때만 날것에서 본문을 건진다
        obj = {"충분": False, "부족": "원고 형식이 깨짐"}
        text = re.sub(r'^\s*\{?\s*"?본문"?\s*:\s*"?', "", raw, flags=re.S)
        text = re.split(r'"\s*,\s*"충분"', text)[0][:3000].strip()
    return {"본문": text, "충분": bool(obj.get("충분")), "부족": str(obj.get("부족") or "").strip()}


def check_citations(text, read):
    """«…» 인용을 뽑아 정식 제목으로 맞추고, 읽지 않은 문서·코퍼스에 없는 이름을 가른다.

    코드가 잡는 것은 여기까지다. 읽은 문서를 엉뚱한 문장에 붙인 것은 사람이 원문과 대조해야 잡힌다.
    """
    raw = re.findall(r"«([^»]+)»", text)
    cited, unknown = [], []
    for c in raw:
        for part in re.split(r"\s*[,·]\s*", c):      # «A, B» 처럼 한 괄호에 둘을 넣는 경우
            title = resolve_title(part, DOCS)
            (cited if title else unknown).append(title or part)
    return {"인용": cited, "허위인용": sorted({c for c in cited if c not in read}),
            "없는문서인용": sorted(set(unknown))}


def strip_invalid(text, read):
    """읽지 않았거나 코퍼스에 없는 «» 표시를 지운다. (고친 글, 지운 이름 목록)."""
    removed = []

    def fix(m):
        keep = []
        for part in re.split(r"\s*[,·]\s*", m.group(1)):
            title = resolve_title(part, DOCS)
            if title in read:
                keep.append(title)
            else:
                removed.append(title or part)
        return f" «{', '.join(keep)}»" if keep else ""

    return re.sub(r"\s*«([^»]+)»", fix, text), removed


def researcher(s):
    """그래프 없이 직접 불러도 된다 — task 와 prior 만 있으면 된다."""
    t, prior = s["task"], s.get("prior") or {}
    read_pos, notes, spent, costs, visits = explore(t, prior, s.get("switches", {}).get("재촉", False))
    draft = write(t, notes, costs, prior.get("본문", "") if prior else "")
    cites = check_citations(draft["본문"], set(read_pos))
    rewrote = False
    if notes and draft["본문"] and not cites["인용"]:
        # 자료를 읽고도 «» 근거 표시를 통째로 빠뜨린 원고는, 읽기 없이 집필만 한 번 다시 시킨다.
        # 재위임(더 읽으러 다시 내보내기)이 아니다 — 재위임은 스스로 부족하다고 신고한 절만 한다.
        again = write(t, notes, costs, prior.get("본문", "") if prior else "",
                      feedback=f"지난 원고의 문장들에 근거가 하나도 없다. 문장마다 '근거' 칸에 "
                               f"[근거로 쓸 수 있는 문서] 의 제목을 적어 다시 써라.\n[지난 원고]\n{draft['본문']}")
        again_cites = check_citations(again["본문"], set(read_pos))
        valid = [c for c in again_cites["인용"] if c in read_pos]
        if valid:
            # 올바른 근거가 하나라도 생겼으면 받는다. 틀린 «» 표시는 코드가 지우고 그 목록을 남긴다
            # (지표의 '제거된인용' 경보로 드러난다). 틀린 것 하나 때문에 전부 버리면 근거 0곳 원고가 남는다.
            text, removed = strip_invalid(again["본문"], set(read_pos))
            draft, rewrote = {**again, "본문": text}, True
            cites = {**check_citations(text, set(read_pos)), "제거된인용": removed}
    sec = {"절": t["절"], "번호": t["번호"], "바퀴": t["바퀴"], "역할": t["역할"],
           "시작문서": t.get("시작문서", ""), "담당문서": t.get("담당문서", []),
           "피하기": t.get("피하기", []), "지시": t["지시"],
           "예산": t["예산"], "이번_읽은글자": spent,
           "읽은문서": list(read_pos), "읽은위치": read_pos, "메모": notes,
           **draft, **{"제거된인용": [], **cites}, "인용보강": rewrote}
    mark = "충분" if sec["충분"] else f"부족({sec['부족'][:24]})"
    mark += " · 인용 보강 재집필" if rewrote else ""
    warn = f" · ⚠ 허위인용 {len(sec['허위인용'])}" if sec["허위인용"] else ""
    return {"sections": [sec], "visited": visits, "cost": costs,
            "log": [f"   ③ {t['역할']} «{t['절'][:18]}» {len(visits)}회 읽기 · {spent:,}/{t['예산']:,}자 · "
                    f"원고 {len(draft['본문'])}자 · 인용 {len(sec['인용'])}곳{warn} · {mark}"]}


# ─── ④ 점검 ─────────────────────────────────────────────────────

def keep_new(new, old):
    """두 번째 원고를 받을지. (받는가, 이유).

    무조건 덮어쓰면, 다시 쓰다가 인용을 빠뜨린 원고가 멀쩡한 첫 원고를 지운다. 그래서
    ① 허위인용이나 없는 문서 인용이 하나라도 있으면 거절하고
    ② 근거로 댄 서로 다른 문서 수가 줄었으면 거절하고
    ③ 그 밖에는 받는다 (새로 읽은 자료가 더해진 원고이므로).
    """
    if new["허위인용"] or new["없는문서인용"]:
        return False, "새 원고에 읽지 않았거나 없는 문서 인용"
    n_new, n_old = len(set(new["인용"])), len(set(old["인용"]))
    if n_new < n_old:
        return False, f"근거 문서 {n_old}→{n_new}건으로 줄어듦"
    return True, f"근거 문서 {n_old}→{n_new}건"


def review(s):
    """자기신고로 부족하다고 한 절만 다시 보낸다. 두 번째 원고는 keep_new 규칙으로 받거나 버린다."""
    p = s["plan"]
    toc, wheel = p["목차"], p["바퀴"]
    adopted, log = dict(p.get("채택", {})), []
    for t in toc:
        drafts = sorted((x for x in s["sections"] if x["절"] == t["절"]), key=lambda x: x["바퀴"])
        if not drafts:
            continue
        newest = drafts[-1]
        if t["절"] not in adopted:
            adopted[t["절"]] = newest["바퀴"]
        elif newest["바퀴"] != adopted[t["절"]]:
            old = next(x for x in drafts if x["바퀴"] == adopted[t["절"]])
            ok, why = keep_new(newest, old)
            if ok:
                adopted[t["절"]] = newest["바퀴"]
            log.append(f"   ④ «{t['절'][:18]}» {newest['바퀴']}바퀴 원고 {'채택' if ok else '버림'} — {why}")
    current = chosen_sections(s["sections"], adopted)
    if not s["switches"]["재위임"]:
        gaps, why = [], "재위임 끔"
    elif wheel >= CONFIG["최대바퀴"]:
        gaps, why = [], "바퀴 상한"
    else:
        gaps = [i for i, t in enumerate(toc) if not current.get(t["절"], {}).get("충분", True)]
        why = "빈 칸 없음" if not gaps else ""
    if not gaps:
        log.append(f"④ 점검   {len(toc)}절 — 종합으로 ({why})")
        return {"plan": {**p, "배치": [], "채택": adopted, "종료": why}, "log": log}
    newtoc = [dict(t) for t in toc]
    for i in gaps:
        prev = current[toc[i]["절"]]
        newtoc[i]["지시"] = (f"{toc[i]['지시']} (재위임: 지난번에 «{'», «'.join(prev['읽은문서']) or '없음'}» 를 "
                             f"읽었지만 '{prev['부족'] or '근거가 모자랐다'}'. 그 빈 칸을 겨냥해 아직 안 본 문서를 찾아라)")
    log.append(f"④ 점검   {len(toc)}절 중 부족 신고 {len(gaps)}절 — "
               f"«{'», «'.join(toc[i]['절'][:14] for i in gaps)}» 재위임")
    return {"plan": {**p, "목차": newtoc, "배치": gaps, "바퀴": wheel + 1, "채택": adopted}, "log": log}


def route(s):
    return "more" if s["plan"]["배치"] else "done"


# ─── ⑤ 종합 ─────────────────────────────────────────────────────

OUTLINE_CHARS = 90   # 코디네이터(편집자)가 절마다 보는 첫머리 길이 — 본문은 보지 않는다

SYNTH_SYSTEM = """너는 보고서를 마무리하는 편집자다. 아래는 조사관들이 각자 쓴 절의 제목과 첫머리다.
본문은 고치지 않는다. 보고서 전체를 여는 머리말과 닫는 맺음말만 쓴다. 각각 세 문장 이내.
머리말·맺음말에는 절 첫머리에 없는 새 사실을 넣지 않는다 — 무엇을 어떤 순서로 다루는지만 안내한다.
JSON 으로만: {"머리말": "...", "맺음말": "..."}"""


def synthesize(s):
    p = s["plan"]
    chosen = chosen_sections(s["sections"], p.get("채택"))
    order = sorted(chosen.values(), key=lambda x: x["번호"])
    outline = "\n".join(f"{i + 1}. {x['절']}: {x['본문'][:OUTLINE_CHARS]}…" for i, x in enumerate(order))
    raw, c = ask(SYNTH_SYSTEM, f"[질문] {s['question']}\n[보고서 제목] {p['제목']}\n[절 목록]\n{outline}",
                 "코디", stage="종합")
    obj = jload(raw, {})
    parts = [f"# {p['제목']}", str(obj.get("머리말") or "").strip()]
    for i, x in enumerate(order):
        parts.append(f"## {i + 1}. {x['절']} _({x['역할']})_\n\n{x['본문']}")
    parts.append(f"## 맺음말\n\n{str(obj.get('맺음말') or '').strip()}")
    report = "\n\n".join(part for part in parts if part.strip())
    return {"report": report, "cost": [c],
            "log": [f"⑤ 종합   {len(order)}절 이어 붙임 → 보고서 {len(report):,}자 "
                    f"(편집자는 절마다 첫머리 {OUTLINE_CHARS}자만 봤다)"]}


# ─── ⑥ 측정 ─────────────────────────────────────────────────────

def record_of(s):
    """실행 결과를 metrics.measure 가 읽는 모양으로. 저장(runs.jsonl)도 이 모양이다."""
    chosen = chosen_sections(s["sections"], s["plan"].get("채택"))
    return {"question": s["question"], "plan": s["plan"], "report": s.get("report", ""),
            "sections": sorted(chosen.values(), key=lambda x: x["번호"]),
            "all_drafts": s["sections"], "visited": s["visited"], "cost": s["cost"]}


def evaluate(s):
    m, detail = metrics.measure(record_of(s), CORPUS)
    return {"metrics": {**m, "_세부": detail}, "log": [f"⑥ 측정   {metrics.one_line(m)}"]}


# ─── 저장 ───────────────────────────────────────────────────────

OUTPUT = BASE / "output"


def corpus_version():
    """어느 코퍼스로 돌렸는지 — 수집일 · 문서 수 · 제목 해시. 코퍼스가 바뀐 실행끼리 섞어 비교하지 않으려고."""
    digest = hashlib.sha1("\n".join(sorted(DOCS)).encode()).hexdigest()[:10]
    return {"수집일": CORPUS.get("_수집일"), "문서수": len(DOCS), "해시": digest}


def _rel(path):
    try:
        return str(path.relative_to(BASE))
    except ValueError:
        return str(path)


_save_lock = threading.Lock()


def save_run(out, qid, label="기본", kind="팀", extra=None):
    """보고서를 output/reports/ 에, 실행 기록 한 줄을 output/runs.jsonl 에 남긴다.

    extra 는 기록에 덧붙일 값 (ablation.py 가 {"실험": …, "반복": …} 을 넣는다). 여러 스레드가 동시에
    불러도 한 줄씩 온전히 쓰이도록 락을 건다.
    """
    stamp = time.strftime("%Y%m%d-%H%M%S")
    run_id = f"{stamp}_{qid}_{kind}_{label}" + (f"_r{extra['반복']}" if extra and "반복" in extra else "")
    (OUTPUT / "reports").mkdir(parents=True, exist_ok=True)
    report_path = OUTPUT / "reports" / f"{run_id}.md"
    report_path.write_text(out.get("report", ""), encoding="utf-8")
    rec = {"id": run_id, "시각": stamp, "질문id": qid, "종류": kind, "설정": label,
           "스위치": out.get("switches", {}), "config": {k: CONFIG[k] for k in (
               "모델", "온도", "절수", "절예산_글자", "문서당_읽기상한", "최대바퀴", "카드_글자")},
           "코퍼스": corpus_version(), "보고서파일": _rel(report_path),
           **record_of(out), "metrics": out.get("metrics", {}), "log": out.get("log", [])}
    rec.pop("all_drafts")
    rec["원고전부"] = out["sections"]            # 채택되지 않은 원고도 남긴다 — 데모에서 나란히 본다
    rec.update(extra or {})
    with _save_lock, open(OUTPUT / "runs.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return run_id, report_path


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
    ap.add_argument("--no-save", action="store_true", help="output/ 에 저장하지 않는다")
    ap.add_argument("--print-report", action="store_true", help="보고서 본문을 출력한다")
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
        return
    if not args.no_save:
        label = "기본" if not off else "끔-" + "-".join(off)
        run_id, path = save_run(out, qid, label)
        print(f"\n저장: {_rel(path)} · output/runs.jsonl ({run_id})")
    if args.print_report:
        print("\n" + out["report"])


if __name__ == "__main__":
    main()
