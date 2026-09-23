#!/usr/bin/env python3
"""나란히 읽기 — 같은 회차의 팀 보고서와 혼자 보고서를 한 화면에 놓는다 (사람이 직접 읽고 판단하려고).

  python compare.py --question Q5                 # ablation-2 의 Q5, 회차마다 탭 하나
  python compare.py --question Q8 --exp ablation-2 --left 기본 --right 혼자

결과: output/compare/<실험>_<질문>_<왼쪽>-vs-<오른쪽>.html (브라우저로 연다, LLM 을 부르지 않는다)
지표는 명백한 실패를 거르는 데만 쓰고, 어느 쪽이 나은지는 이 화면을 읽고 사람이 판단한다(강의 4-c).
"""
import argparse
import html
import json
import re
import sys

import graph

# 질문별 읽기 점검 — 정답표가 아니라 '무엇을 눈여겨볼지'다
CHECKS = {
    "Q5": ["여성 수상자 18명 중 몇 명을 다뤘나? (라겔뢰프 1909 … 한강 2024)",
           "시대에 따른 '흐름'을 실제로 보여 주나, 아니면 한 명씩 나열만 하나?",
           "여성 수상자가 아닌 사람·작품이 섞였나? (예: 『설국』은 가와바타 야스나리의 작품)",
           "«근거» 로 댄 문서에 그 문장 내용이 정말 있나? — 의심 가는 문장 하나를 원문과 대조",
           "머리말·맺음말이 본문에 없는 새 주장을 하나?",
           "시대 절에 그 기간 밖에 상을 받은 수상자가 섞였나? (예: 옐리네크 2004 · 레싱 2007 · 먼로 2013)"],
    "Q8": ["사르트르와 카뮈의 '이어짐'과 '갈라짐'을 둘 다 다뤘나?",
           "대표작(『존재와 무』 『구토』 『이방인』 『페스트』 『시지프 신화』)을 근거로 댔나?",
           "두 사람을 뒤섞어 쓴 문장이 있나?",
           "빠진 비교 절(「관계」 등)의 물음이 사람별 절 안에서 다뤄졌나, 아니면 보고서에서 사라졌나?"],
    "Q2": ["선정 주체·기준·절차를 모두 다뤘나?", "한 문서만으로 충분한 질문인데 절을 나눈 것이 도움이 됐나?"],
}
WOMEN = ["셀마 라겔뢰프", "그라치아 델레다", "시그리드 운세트", "펄 S. 벅", "가브리엘라 미스트랄", "넬리 작스",
         "네이딘 고디머", "토니 모리슨", "비스와바 심보르스카", "엘프리데 옐리네크", "도리스 레싱", "헤르타 뮐러",
         "앨리스 먼로", "스베틀라나 알렉시예비치", "올가 토카르추크", "루이즈 글릭", "아니 에르노", "한강 (작가)"]


def render_md(text):
    """보고서 마크다운의 제목·문단만 HTML 로. «근거» 와 《작품》 은 표시를 달리한다."""
    out = []
    for block in re.split(r"\n\s*\n", text.strip()):
        block = block.strip()
        if not block:
            continue
        esc = html.escape(block)
        esc = re.sub(r"«([^»]+)»", r'<span class="cite">\1</span>', esc)
        esc = re.sub(r"《([^》]+)》", r'<span class="work">《\1》</span>', esc)
        esc = re.sub(r"_\(([^)]+)\)_", r'<span class="role">\1</span>', esc)
        if esc.startswith("## "):
            out.append(f"<h3>{esc[3:]}</h3>")
        elif esc.startswith("# "):
            out.append(f"<h2>{esc[2:]}</h2>")
        else:
            out.append(f"<p>{esc}</p>")
    return "\n".join(out)


def who_read(run):
    """절마다 누가 무엇을 읽었는지 (데모와 같은 정보, 짧게)."""
    rows = []
    orders = {t["절"]: t.get("지시", "") for t in run.get("plan", {}).get("목차", [])}
    for s in run["sections"]:
        docs = s.get("읽은문서", [])
        order = orders.get(s["절"], "")
        rows.append(f"<li><b>{html.escape(s['절'])}</b> <span class='role'>{html.escape(s.get('역할', ''))}</span>"
                    f" — {html.escape(', '.join(docs)) or '(없음)'}"
                    + (f"<br><span class='role'>지시: {html.escape(order)}</span>" if order else "") + "</li>")
    if run["종류"] == "혼자":
        order = " → ".join(dict.fromkeys(v[1] for v in run["visited"]))
        return f"<p class='meta'>읽은 순서: {html.escape(order)}</p>"
    return f"<ul class='meta'>{''.join(rows)}</ul>"


def women_covered(run):
    text = run["report"]
    names = [w for w in WOMEN if re.sub(r"\s*\(.*\)$", "", w).split()[-1] in text]
    return names


def column(run, qid):
    m = run["metrics"]
    extra = ""
    if qid == "Q5":
        names = women_covered(run)
        extra = f" · 여성 수상자 이름 언급 <b>{len(names)}/{len(WOMEN)}</b>"
    return f"""<section class="col">
  <div class="head"><span class="tag {'team' if run['종류'] == '팀' else 'solo'}">{html.escape(run['설정'])}</span>
    <span class="nums">근거율 {m['근거율']}% · 읽은문서 {m['읽은문서']} · 편중 {m['인용편중']}% · {m['보고서자수']:,}자{extra}
      · 시대불일치 {m.get('시대불일치', 0)}</span></div>
  <details><summary>누가 무엇을 읽었나</summary>{who_read(run)}</details>
  <article>{render_md(run['report'])}</article>
</section>"""


PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>나란히 읽기 {qid}</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&display=swap');
@import url('https://cdn.jsdelivr.net/npm/pretendard@1.3.9/dist/web/static/pretendard.min.css');
/* 터미널 콘솔 디자인 시스템(범용판) — app.py · my-graph-agent 와 같은 토큰 */
:root {{ --surface-0:#050706; --surface-1:#0a0d0c; --surface-2:#0e1412; --surface-sel:#1a2621;
        --line:#1d2723; --line-soft:#141c19; --line-control:#2c3a35;
        --ink-hi:#e8efeb; --ink:#cfd8d3; --ink-dim:#8b9a93; --ink-faint:#3c4a44;
        --accent:#4ee08a; --warn:#e8c04e; --on-accent:#05100a;
        --mono:'JetBrains Mono','Pretendard',monospace; }}
body {{ margin:0; background:var(--surface-0); color:var(--ink); font:13px/1.9 var(--mono); }}
header {{ padding:20px 16px 8px; max-width:1400px; margin:0 auto; }}
h1 {{ font-size:20px; margin:0 0 4px; color:var(--ink-hi); }} .q {{ color:var(--ink-dim); margin:0 0 12px; }}
.label {{ font-size:11px; letter-spacing:.14em; color:var(--accent); text-transform:uppercase; }}
.checks {{ background:var(--surface-1); border:1px solid var(--line); padding:10px 16px; margin:0 0 12px; }}
.checks li {{ margin:2px 0; color:var(--ink-dim); }}
.tabs {{ display:flex; gap:0; flex-wrap:wrap; border-bottom:1px solid var(--line); }}
.tabs button {{ border:none; background:transparent; color:var(--ink-dim); padding:6px 14px; cursor:pointer; font:inherit; font-size:12px; }}
.tabs button[aria-selected="true"] {{ background:var(--surface-sel); color:var(--ink-hi); box-shadow:inset 0 -2px 0 var(--accent); }}
main {{ max-width:1400px; margin:0 auto; padding:12px 16px 40px; }}
.pair {{ display:none; grid-template-columns:1fr 1fr; gap:16px; }} .pair.on {{ display:grid; }}
@media (max-width: 860px) {{ .pair.on {{ grid-template-columns:1fr; }} }}
.col {{ background:var(--surface-1); border:1px solid var(--line); padding:14px 18px; min-width:0; }}
.head {{ display:flex; flex-wrap:wrap; gap:8px; align-items:baseline; border-bottom:1px solid var(--line); padding-bottom:8px; }}
.tag {{ display:inline-flex; align-items:center; gap:7px; padding:2px 9px; font-size:11.5px; letter-spacing:.04em; border:1px solid; }}
.tag::before {{ content:""; width:6px; height:6px; background:currentColor; }}
.tag.team {{ color:var(--accent); }} .tag.solo {{ color:var(--warn); }}
.nums {{ color:var(--ink-dim); font-size:11.5px; }}
details {{ margin:8px 0; font-size:12px; }} summary {{ cursor:pointer; color:var(--ink-dim); }}
.meta {{ color:var(--ink-dim); font-size:12px; }}
h2 {{ font-size:17px; margin:14px 0 6px; color:var(--ink-hi); }} h3 {{ font-size:14px; margin:18px 0 4px; color:var(--ink-hi); }}
.cite {{ border:1px solid var(--accent); color:var(--accent); background:rgba(78,224,138,.06); padding:0 5px; font-size:11px; white-space:nowrap; }}
.work {{ color:var(--ink-hi); }} .role {{ color:var(--ink-faint); font-size:11.5px; font-style:normal; }}
footer {{ color:var(--ink-faint); font-size:11.5px; max-width:1400px; margin:0 auto; padding:0 16px 24px; }}
</style></head><body>
<header><div class="label">[ SIDE BY SIDE ]</div><h1>나란히 읽기 — {qid} · {left} 대 {right}</h1><p class="q">{question}</p>
<div class="checks"><span class='label'>[ 읽으며 볼 것 ]</span> 정답표가 아니라 눈여겨볼 점<ul>{checks}</ul></div>
<div class="tabs" role="tablist">{tabs}</div></header>
<main>{pairs}</main>
<footer>실험 {exp} · 같은 회차의 두 실행을 짝지었다. {note}
<span class="cite">초록 칩</span> = 근거 문서, <span class="work">《밝은 글씨》</span> = 작품 이름.</footer>
<script>
document.querySelectorAll('.tabs button').forEach(b => b.addEventListener('click', () => {{
  document.querySelectorAll('.tabs button').forEach(x => x.setAttribute('aria-selected', x === b));
  document.querySelectorAll('.pair').forEach(p => p.classList.toggle('on', p.id === b.dataset.for));
}}));
</script></body></html>"""


def main():
    ap = argparse.ArgumentParser(description="나란히 읽기 화면 만들기")
    ap.add_argument("--question", default="Q5")
    ap.add_argument("--exp", default="ablation-2")
    ap.add_argument("--left", default="기본")
    ap.add_argument("--right", default="혼자")
    args = ap.parse_args()
    path = graph.OUTPUT / "runs.jsonl"
    if not path.exists():
        sys.exit("output/runs.jsonl 이 없다 — 먼저 python ablation.py 로 실험을 돌린다")
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = [r for r in rows if r.get("실험") == args.exp and r["질문id"] == args.question]
    reps = sorted({r["반복"] for r in rows})
    tabs, pairs = [], []
    for i, rep in enumerate(reps):
        left = next((r for r in rows if r["설정"] == args.left and r["반복"] == rep), None)
        right = next((r for r in rows if r["설정"] == args.right and r["반복"] == rep), None)
        if not (left and right):
            continue
        pid = f"r{rep}"
        tabs.append(f'<button role="tab" data-for="{pid}" aria-selected="{str(i == 0).lower()}">{rep}회차</button>')
        pairs.append(f'<div class="pair{" on" if i == 0 else ""}" id="{pid}">'
                     f'{column(left, args.question)}{column(right, args.question)}</div>')
    if not pairs:
        sys.exit(f"짝지을 실행이 없다 — 실험 {args.exp} · {args.question} · {args.left} 대 {args.right}")
    _, question = graph.load_question(args.question)
    checks = "".join(f"<li>{html.escape(c)}</li>" for c in CHECKS.get(args.question, []))
    page = PAGE.format(qid=args.question, left=args.left, right=args.right, question=html.escape(question),
                       checks=checks, tabs="".join(tabs), pairs="".join(pairs), exp=args.exp,
                       note=("혼자 쪽은 그 회차 팀 실행이 실제로 읽은 글자만큼 예산을 받았다." if args.right == "혼자"
                             else "두 쪽 모두 팀 실행이고 스위치 하나만 다르다 — 같은 회차는 이웃한 시각에 돌았다."))
    out = graph.OUTPUT / "compare" / f"{args.exp}_{args.question}_{args.left}-vs-{args.right}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    print(f"저장: {graph._rel(out)} · 회차 {len(pairs)}개")


if __name__ == "__main__":
    main()
