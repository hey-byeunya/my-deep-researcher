"""데모 — 사람이 직접 질문해 보고, 절마다 누가 무엇을 읽고 무엇을 썼는지 들여다보는 화면.

  .venv/bin/streamlit run app.py

무엇을 할까요
  ① 질문하기   — 질문을 직접 쓰고 Enter. .env 의 OPENAI_API_KEY 가 필요하다.
  ② 데모 보기  — output/runs.jsonl 에 저장된 실행(팀 · 혼자 · 절제 설정)을 고른다. API 키가 없어도 된다.
  ③ 질문 선택  — 준비한 질문 10건(data/questions.json) 중 하나를 골라 돌린다.
숫자(지표)는 보조다. 화면의 중심은 '절별 작업' 탭 — 코디네이터가 무엇을 배정했고, 서브에이전트가 무엇을
읽어 무엇을 썼는지, 어느 원고가 채택됐는지를 절마다 보여 준다.
"""
import html
import json
import os

import streamlit as st

import collect_corpus
import compare
import graph
import metrics

st.set_page_config(page_title="노벨 문학상 딥리서처", page_icon="📚", layout="wide")

# ── 디자인 토큰 → CSS. my-graph-agent 와 같은 '터미널 콘솔 디자인 시스템(범용판)'을 따른다.
#    테마(.streamlit/config.toml)로 못 옮기는 것만 여기서 맡는다. 이름은 디자인 시스템의 의미 이름 그대로.
CSS = """<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&display=swap');
@import url('https://cdn.jsdelivr.net/npm/pretendard@1.3.9/dist/web/static/pretendard.min.css');

:root {
  --surface-0:#050706; --surface-1:#0a0d0c; --surface-2:#0e1412; --surface-3:#111a17;
  --surface-sel:#1a2621;
  --line:#1d2723; --line-soft:#141c19; --line-control:#2c3a35; --track:#233029;
  --ink-hi:#e8efeb; --ink:#cfd8d3; --ink-dim:#8b9a93; --ink-faint:#3c4a44;
  --accent:#4ee08a; --warn:#e8c04e; --danger:#ff8b74; --danger-line:#e2604b;
  --on-accent:#05100a;
  --mono:'JetBrains Mono','Pretendard',monospace;
}
html, body, [class*="st-"], .stMarkdown, button, input, textarea, select { font-family: var(--mono) !important; }
/* 아이콘은 글리프 폰트라 위 규칙에서 뺀다 — 안 빼면 'keyboard_arrow_right' 가 글자로 찍힌다 */
[data-testid="stIconMaterial"], .material-icons, .material-symbols-rounded {
  font-family: 'Material Symbols Rounded' !important; }
.block-container { padding-top: 24px; max-width: 1280px; }
header[data-testid="stHeader"] { background: transparent; }

/* 액자 — 화면에 하나. 헤더 바 왼쪽은 무엇의 화면인지 */
.frame-bar { display:flex; align-items:center; gap:10px; padding:9px 14px;
  background:var(--surface-2); border:1px solid var(--line); border-bottom:none;
  font-size:12px; color:var(--ink-dim); }
.frame-bar .dots i { display:inline-block; width:8px; height:8px; border-radius:50%;
  background:var(--line-control); margin-right:5px; }
.frame-bar .dots i:last-child { background:var(--accent); }
.frame-bar b { color:var(--ink-hi); font-weight:500; }
.frame-body { border:1px solid var(--line); background:var(--surface-1);
  padding:18px 16px 16px; box-shadow: inset 0 0 90px rgba(78,224,138,.035); margin-bottom:18px; }
.frame-body .cmd { font-size:12.5px; color:var(--ink); margin-bottom:10px; }
.frame-body .cmd span { color:var(--accent); }
.frame-body h1 { font-size:26px; font-weight:700; color:var(--ink-hi); margin:0 0 8px; padding:0; letter-spacing:0; }
.frame-body p { font-size:13px; line-height:1.95; color:var(--ink-dim); margin:0; }
.frame-body p b { color:var(--ink); font-weight:500; }
/* 파이프라인 단계 — 사각 칩, 번호만 강조색 */
.pipe { display:flex; flex-wrap:wrap; gap:6px; margin:12px 0 0; }
.pipe span { border:1px solid var(--line-control); padding:2px 9px; font-size:11.5px; color:var(--ink); }
.pipe b { color:var(--accent); font-weight:500; }

/* 섹션 라벨 — [ X ] 11px · 자간 .14em · 강조색 */
.label { font-size:11px; letter-spacing:.14em; color:var(--accent); text-transform:uppercase; margin:18px 0 8px; }
.label.plain { color:var(--ink-dim); }

/* 상태 배지 — 대문자 스네이크, 사각 점 6px */
.badge { display:inline-flex; align-items:center; gap:7px; padding:3px 9px;
  font-size:11.5px; letter-spacing:.04em; border:1px solid; }
.badge::before { content:""; width:6px; height:6px; background:currentColor; }
.badge.ok { color:var(--accent); border-color:var(--accent); }
.badge.warn { color:var(--warn); border-color:var(--warn); }
.badge.danger { color:var(--danger); border-color:var(--danger-line); }
.badge-row { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin:4px 0 10px;
  font-size:11.5px; color:var(--ink-faint); }

/* 계기 — 수치 17/700 ink-hi, 라벨 ink-dim */
[data-testid="stMetric"] { background:var(--surface-1); border:1px solid var(--line); padding:10px 14px; }
[data-testid="stMetricLabel"] p { font-size:11.5px; color:var(--ink-dim); }
[data-testid="stMetricValue"] { font-size:17px; font-weight:700; color:var(--ink-hi); }

/* 탭 — 테두리 없이 면을 반전 */
.stTabs [data-baseweb="tab-list"] { gap:0; border-bottom:1px solid var(--line); }
.stTabs [data-baseweb="tab"] { padding:6px 14px; font-size:12px; color:var(--ink-dim); }
.stTabs [aria-selected="true"] { background:var(--surface-sel); color:var(--ink-hi); }
.stTabs [data-baseweb="tab-highlight"] { background:var(--accent); }

/* 보고서 · 원고 본문 — 근거 칩은 강조색 테두리, 작품 이름은 warn 대신 ink-hi */
.stMarkdown p { line-height:1.9; }
.cite { border:1px solid var(--accent); color:var(--accent); padding:0 5px; font-size:11px;
  white-space:nowrap; background:rgba(78,224,138,.06); }
.work { color:var(--ink-hi); }
.role { color:var(--ink-faint); font-size:11.5px; }
/* 담당문서 · 읽은 순서 칩 — 사각, 공용 서가는 점선 */
.chip { display:inline-block; border:1px solid var(--line-control); padding:0 7px; margin:2px 2px;
  font-size:11.5px; color:var(--ink); }
.chip.shared { border-style:dashed; color:var(--ink-dim); }
.alarm { color:var(--danger); font-size:12px; }

/* 알림 · 빈 자리 */
.notice { border:1px solid var(--warn); padding:10px 14px; font-size:12px; color:var(--ink); line-height:1.85; }
.notice b { color:var(--warn); font-weight:500; }
.empty { border:1px dashed var(--line-control); padding:16px; text-align:center; font-size:12px; color:var(--ink-dim); }

/* 버튼 — 채운 강조 버튼은 화면당 하나 */
.stFormSubmitButton button[kind="primaryFormSubmit"],
.stButton button[kind="primary"] { background:var(--accent); color:var(--on-accent);
  border:1px solid var(--accent); font-weight:700; }
/* 입력 칸 — 안쪽은 페이지보다 어두운 surface-0, 질문 칸은 한 단계 밝은 테두리, 포커스는 강조색 */
.stTextInput input, .stSelectbox [data-baseweb="select"] > div { background:var(--surface-0) !important; }
.stTextInput .react-aria-TextField > div, .stSelectbox .react-aria-ComboBox > div {
  border-color:var(--line-control) !important; }
.st-key-ask_q .react-aria-TextField > div { border-color:var(--ink-dim) !important; }
.stTextInput .react-aria-TextField > div:focus-within,
.stSelectbox .react-aria-ComboBox > div:focus-within { border-color:var(--accent) !important; }
.st-key-ask_q label p { color:var(--ink) !important; }
label p { font-size:11.5px !important; color:var(--ink-dim) !important; }

/* 사이드바 */
section[data-testid="stSidebar"] { border-right:1px solid var(--line); }
section[data-testid="stSidebar"] .label:first-child { margin-top:4px; }
.kv { font-size:11.5px; line-height:1.85; color:var(--ink-dim); }
.kv b { color:var(--ink-hi); font-weight:700; }
</style>"""
st.markdown(CSS, unsafe_allow_html=True)

QUESTIONS = json.loads((graph.BASE / "data" / "questions.json").read_text(encoding="utf-8"))["questions"]
RUNS_FILE = graph.OUTPUT / "runs.jsonl"
ABLATION_FILE = graph.OUTPUT / "ablation.json"
MODES = ["질문하기", "데모 보기", "질문 선택"]
HAS_KEY = os.getenv("OPENAI_API_KEY", "").startswith("sk-")


@st.cache_data
def load_runs(mtime):   # mtime 은 캐시 열쇠 — 파일이 바뀌면 다시 읽는다
    if not RUNS_FILE.exists():
        return []
    rows = [json.loads(line) for line in RUNS_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    return rows[::-1]                                   # 최근 것부터


def runs():
    return load_runs(RUNS_FILE.stat().st_mtime if RUNS_FILE.exists() else 0)


@st.cache_data
def corpus_tokens(n_docs):   # n_docs 는 캐시 열쇠 — 코퍼스가 바뀌면 다시 센다
    return collect_corpus.count_tokens(graph.DOCS.values())


def chips(titles, shared=graph.SHARED):
    return " ".join(f"<span class='chip{' shared' if t in shared else ''}'>{html.escape(t)}</span>"
                    for t in titles) or "<span class='role'>(없음)</span>"


def run_label(r):
    return f"{r['질문id']} · {r['종류']} · {r['설정']}" + (f" · {r.get('실험')} r{r['반복']}" if r.get("실험") else "") \
        + f" · {r['시각']}"


def label(text):
    st.markdown(f"<div class='label'>[ {html.escape(text)} ]</div>", unsafe_allow_html=True)


# ─── 첫 화면 머리 ─────────────────────────────────────────────────

kinds = {k: sum(1 for v in graph.KINDS.values() if v == k) for k in ("수상자", "작품", "공통")}
tokens = corpus_tokens(len(graph.DOCS)) or 0
window = f"모델 창의 {tokens / collect_corpus.MODEL_WINDOW:.1f}배" if tokens else "모델 창보다 크다"
st.markdown(
    "<div class='frame-bar'><span class='dots'><i></i><i></i><i></i></span>"
    "my-deep-researcher — <b>nobel-literature</b> · deep-research</div>"
    "<div class='frame-body'>"
    "<div class='cmd'><span>➜</span> python graph.py --question</div>"
    "<h1>노벨 문학상 딥리서처</h1>"
    f"<p>한국어 위키백과의 노벨 문학상 수상자 {kinds['수상자']}명 · 대표작 {kinds['작품']}편 · 공통 문서 {kinds['공통']}건"
    f"(총 {sum(map(len, graph.DOCS.values())):,}자 — {window})을 한 번에 읽을 수 없어서, "
    "코디네이터가 목차를 짜 구역을 나누고 서브에이전트들이 동시에 자기 구역만 읽어 장문 보고서를 쓴다.</p>"
    "<p><b>절마다 누가 무엇을 읽고 무엇을 썼는지 그대로 보여 주고, 읽지 않은 문서를 근거로 대면 코드가 잡아낸다.</b> "
    "코디네이터는 원문을 보지 않는다 — 문서 카드만 본다.</p>"
    "<div class='pipe'><span><b>①</b> 기획 · 목차와 담당문서</span><span><b>②</b> 배치 · 동시 파견</span>"
    "<span><b>③</b> 서브에이전트 · 예산만큼 읽고 쓰기</span><span><b>④</b> 점검 · 부족 신고만 재위임</span>"
    "<span><b>⑤</b> 종합 · 머리말·맺음말만</span><span><b>⑥</b> 측정 · 정답표 없이</span></div>"
    "</div>", unsafe_allow_html=True)


# ─── 사이드바 — 코퍼스 · 실험 요약 ─────────────────────────────────

with st.sidebar:
    label("CORPUS")
    c1, c2 = st.columns(2)
    c1.metric("문서", len(graph.DOCS))
    c2.metric("토큰", f"{tokens / 10000:.0f}만" if tokens else "—")
    st.markdown(f"<div class='kv'>수상자 {kinds['수상자']} · 작품 {kinds['작품']} · 공통 {kinds['공통']}<br>"
                "한국어 위키백과(CC BY-SA 4.0) · 수상 연도는 노벨상 공식 API<br>"
                "링크는 본문에 실제로 나오는 것만 남겼다</div>", unsafe_allow_html=True)
    label("ABLATION")
    if ABLATION_FILE.exists():
        ab = json.loads(ABLATION_FILE.read_text(encoding="utf-8"))
        q5 = ab["결과"].get("Q5", {})
        table_ = [{"설정": k, "근거%": round(v["근거율"]["평균"]), "예산%": round(v["예산소진율"]["평균"])}
                  for k, v in q5.items()]
        st.caption("Q5(흩어진 질문) · 3회 평균 · 근거율 · 예산 소진율")
        st.dataframe(table_, hide_index=True, width="stretch")
        base, off, solo = (q5.get(k) for k in ("기본", "배정끔", "혼자"))
        if base and off and solo:   # 문구의 숫자도 ablation.json 에서 — 실험을 다시 돌리면 같이 바뀐다
            st.markdown(f"<div class='kv'>배정을 끄면 예산을 {off['예산소진율']['평균']:.0f}%만 쓰고 근거율이 "
                        f"{base['근거율']['평균']:.0f}% → {off['근거율']['평균']:.0f}%로 떨어진다. 혼자는 근거율이 "
                        f"{solo['근거율']['평균']:.0f}%로 높지만 문서를 더 적게({solo['읽은문서']['평균']:.0f} 대 "
                        f"{base['읽은문서']['평균']:.0f}) 읽고 한두 문서에 기대 쓴다 — 어느 쪽이 나은지는 "
                        "'나란히 보기'로 읽어 본다.</div>", unsafe_allow_html=True)
    else:
        st.markdown("<div class='kv'>절제 실험 결과가 없다 — python ablation.py</div>", unsafe_allow_html=True)


# ─── 결과 화면 (세 모드가 함께 쓴다) ─────────────────────────────────

def render_team_work(r):
    st.markdown("##### ① 기획 — 코디네이터가 짠 목차와 배정")
    st.caption("코디네이터는 원문을 보지 않는다. 문서 카드(제목 · 수상 연도 · 앞 120자)만 보고 절마다 "
               "역할 · 시작문서 · 담당문서 · 예산을 정했다. 점선 칩은 여러 절이 함께 쓰는 공용 서가.")
    for t in r["plan"]["목차"]:
        st.markdown(f"- **{html.escape(t['절'])}** <span class='role'>{html.escape(t.get('역할', ''))} · "
                    f"예산 {t.get('예산', 0):,}자</span><br>&nbsp;&nbsp;담당 {chips(t.get('담당문서', []))}",
                    unsafe_allow_html=True)
    fixes = r["plan"].get("교정", [])
    if fixes:
        with st.expander(f"⚠ 코드가 고친 배정 {len(fixes)}건 — 모델이 지어낸 제목 · 겹친 구역 · 형식 단위 절"):
            for f in fixes:
                st.write("· " + f)
    st.markdown("##### ③ 서브에이전트 — 절마다 무엇을 읽고 무엇을 썼나")
    adopted = r["plan"].get("채택", {})
    drafts = r.get("원고전부") or r["sections"]
    for t in r["plan"]["목차"]:
        mine = sorted((d for d in drafts if d["절"] == t["절"]), key=lambda d: d["바퀴"])
        if not mine:
            continue
        final = mine[-1] if t["절"] not in adopted else next(
            (d for d in mine if d["바퀴"] == adopted[t["절"]]), mine[-1])
        badge = "충분" if final.get("충분") else f"부족 신고 — {final.get('부족', '')}"
        with st.expander(f"**{t['절']}** · {final['역할']} · {len(mine)}바퀴 · {badge}", expanded=True):
            for d in mine:
                tag = (" · ✅ 채택" if d is final else " · ✖ 버림") if len(mine) > 1 else ""
                st.markdown(f"**{d['바퀴']}바퀴 원고{tag}**")
                cols = st.columns([2, 3])
                with cols[0]:
                    st.markdown("피하기(남의 구역) " + chips(d.get("피하기", [])[:12])
                                + (" …" if len(d.get("피하기", [])) > 12 else ""), unsafe_allow_html=True)
                    reads = [v for v in r["visited"] if v[0] == d["절"] and v[3] == d["바퀴"]]
                    st.markdown(f"**읽은 문서** ({d.get('이번_읽은글자', 0):,} / {d.get('예산', 0):,}자)")
                    st.dataframe([{"문서": v[1], "글자": v[2]} for v in reads], hide_index=True,
                                 width="stretch", height=min(38 * (len(reads) + 1), 260))
                    with st.popover("메모 보기 — 원문을 읽고 남긴 요약"):
                        for doc, note in d.get("메모", []):
                            st.markdown(f"**{doc}** — {note}")
                with cols[1]:
                    st.markdown(compare.render_md(d.get("본문", "")), unsafe_allow_html=True)
                    warn = [f"{key} {', '.join(d[key])}" for key in ("허위인용", "없는문서인용", "제거된인용")
                            if d.get(key)]
                    if d.get("인용보강"):
                        warn.append("인용이 없어 읽기 없이 한 번 다시 썼다")
                    if warn:
                        st.markdown("<span class='alarm'>⚠ " + " · ".join(map(html.escape, warn)) + "</span>",
                                    unsafe_allow_html=True)
                    st.caption("근거로 댄 문서 " + ", ".join(dict.fromkeys(d.get("인용", []))))
                if "재위임" in d.get("지시", ""):
                    st.caption("재위임 지시: " + d["지시"].split("(재위임:", 1)[-1].rstrip(")"))
    st.markdown("##### ⑤ 종합")
    st.caption("편집자(코디네이터)는 절마다 첫머리 90자만 보고 머리말 · 맺음말을 썼다. 본문은 원고 그대로다.")


def render_run(r):
    m = r["metrics"]
    st.subheader(r["plan"].get("제목") or r["question"])
    st.caption(f"[{r['질문id']}] {r['question']}  \n**{r['종류']} · {r['설정']}**"
               + (f" · {r.get('실험')} {r['반복']}회차" if r.get("실험") else "") + f" · {r['시각']}")
    alarm = metrics.alarms(m)
    kind = "TEAM" if r["종류"] == "팀" else "SOLO"
    badges = [f"<span class='badge ok'>{kind}_{html.escape(str(r['설정'])).upper()}</span>",
              "<span class='badge ok'>NO_ALARM</span>" if not alarm else
              f"<span class='badge warn'>ALARM × {sum(alarm.values())}</span>"]
    if m.get("허위인용") or m.get("없는문서인용"):
        badges.append("<span class='badge danger'>BAD_CITATION</span>")
    st.markdown(f"<div class='badge-row'>{''.join(badges)}<span>{html.escape(r['id'])}</span></div>",
                unsafe_allow_html=True)
    top = st.columns(5)
    top[0].metric("절", len(r["sections"]))
    top[1].metric("읽은 문서", m["읽은문서"])
    top[2].metric("근거율", f"{m['근거율']:.0f}%")
    top[3].metric("LLM 호출", m["LLM호출"])
    top[4].metric("경보", sum(alarm.values()) if alarm else 0,
                  help=" · ".join(f"{k} {v}" for k, v in alarm.items()) or "없음")

    tab_work, tab_report, tab_metrics, tab_side = st.tabs(["🔎 절별 작업", "📄 보고서", "📊 지표", "↔ 나란히 보기"])

    with tab_work:
        if r["종류"] == "혼자":
            st.info("혼자 하는 대조군 — 나누지 않았다. 한 에이전트가 아래 순서로 읽고 보고서 전체를 한 번에 썼다.")
            order = [v[1] for v in r["visited"]]
            st.markdown("**읽은 순서** " + chips(list(dict.fromkeys(order))), unsafe_allow_html=True)
            st.caption(f"읽은 글자 {sum(v[2] for v in r['visited']):,}자 = 짝 팀 실행이 실제로 읽은 양 "
                       f"({m.get('_짝', '짝 정보 없음')})")
        else:
            render_team_work(r)

    with tab_report:
        st.markdown(compare.render_md(r["report"]), unsafe_allow_html=True)
        st.download_button("보고서 내려받기(.md)", r["report"], file_name=f"{r['id']}.md")

    with tab_metrics:
        st.caption("정답표도 판정 모델도 쓰지 않는다. 신호는 설정끼리 견줄 때, 경보는 0 이어야 한다. "
                   "지표는 명백한 실패를 거르는 데만 쓰고 순위표로 쓰지 않는다.")
        iso = st.columns(3)
        iso[0].metric("격리율(수업 정의)", f"{m['격리율']}%", help=metrics.METRICS["격리율"][2])
        iso[1].metric("코디네이터 카드 열람률", f"{m['코디카드열람률']}%", help="원문은 0자 — 카드만 봤다")
        iso[2].metric("절간 중복 문서", m["절간중복문서"], help=metrics.METRICS["절간중복문서"][2])
        table = [{"종류": kind, "지표": name, "값": m.get(name), "보는 장치": device, "설명": desc}
                 for name, (kind, device, desc) in metrics.METRICS.items()]
        st.dataframe(table, hide_index=True, width="stretch", height=36 * (len(table) + 1) + 4)

    with tab_side:
        others = [x for x in runs() if x["질문id"] == r["질문id"] and x["id"] != r["id"]]
        if not others:
            st.info("같은 질문의 다른 실행이 없다.")
            return
        default = next((i for i, x in enumerate(others) if x["종류"] != r["종류"]), 0)
        other = st.selectbox("견줄 실행", others, index=default, format_func=run_label, key=f"side_{r['id']}")
        left, right = st.columns(2)
        for col, x in ((left, r), (right, other)):
            with col:
                xm = x["metrics"]
                st.markdown(f"#### {x['종류']} · {x['설정']}")
                st.caption(f"근거율 {xm['근거율']}% · 읽은문서 {xm['읽은문서']} · 편중 {xm['인용편중']}% · "
                           f"{xm['보고서자수']:,}자")
                st.markdown(compare.render_md(x["report"]), unsafe_allow_html=True)


# ─── 돌리기 (질문하기 · 질문 선택이 함께 쓴다) ─────────────────────────

def switches_panel(key):
    with st.expander("스위치 — 장치를 하나씩 끄고 켜 본다 (기본값이 최종 설정)"):
        cols = st.columns(len(graph.SWITCHES))
        return {name: cols[i].checkbox(name, value=val, key=f"{key}_{name}")
                for i, (name, val) in enumerate(graph.SWITCHES.items())}


def run_live(question, qid, sw):
    box = st.status("파이프라인을 돌리는 중… (30초 안팎, LLM 15~25회)", expanded=True)
    seen, final = 0, None
    for state in graph.stream(question, **sw):
        for line in state["log"][seen:]:
            box.write(line)
        seen, final = len(state["log"]), state
    box.update(label="끝 — output/runs.jsonl 에 저장했다", state="complete", expanded=False)
    off = [k for k, v in sw.items() if v != graph.SWITCHES[k]]
    run_id, _ = graph.save_run(final, qid, label="기본" if not off else "바꿈-" + "-".join(off))
    load_runs.clear()
    return run_id


def show_last(mode_key):
    run_id = st.session_state.get(mode_key)
    hit = next((r for r in runs() if r["id"] == run_id), None) if run_id else None
    if hit:
        label("RESULT")
        render_run(hit)
    return hit


# ─── 무엇을 할까요 ───────────────────────────────────────────────

label("무엇을 할까요")
mode = st.radio("무엇을 할까요", MODES, horizontal=True, key="mode", label_visibility="collapsed",
                captions=["직접 쓴 질문으로 돌린다", "저장된 실행을 키 없이 본다", "준비한 질문 10건 중 고른다"])

if mode == "질문하기":
    if not HAS_KEY:
        st.markdown("<div class='notice'><b>OPENAI_API_KEY 가 없다</b> — .env 를 채우면 돌릴 수 있다. "
                    "그동안은 데모 보기로 저장된 실행을 본다.</div>", unsafe_allow_html=True)
    # st.form 으로 감싸야 칸에서 Enter 를 눌러도 '물어보기' 를 누른 것과 같이 제출된다
    with st.form("ask_form", border=False):
        q = st.text_input("질문", key="ask_q",
                          placeholder="예: 한강의 작품 세계는 『채식주의자』에서 『희랍어 시간』까지 어떻게 이어지는가?")
        sw = switches_panel("ask")
        submitted = st.form_submit_button("물어보기 ↵", type="primary", disabled=not HAS_KEY)
    if submitted and q.strip():
        st.session_state["ask_run"] = run_live(q.strip(), "자유", sw)
    if not show_last("ask_run"):
        st.markdown("<div class='empty'>아직 질문이 없다 — 질문을 쓰고 Enter. "
                    "여러 수상자에 흩어진 질문일수록 나눠 읽는 힘이 드러난다.</div>", unsafe_allow_html=True)

elif mode == "데모 보기":
    rows = runs()
    if not rows:
        st.markdown("<div class='empty'>저장된 실행이 없다 — python graph.py --question Q5</div>",
                    unsafe_allow_html=True)
        st.stop()
    c = st.columns([1, 1.3, 1.3, 3])
    qids = sorted({r["질문id"] for r in rows}, key=lambda x: (x == "자유", len(x), x))
    qid = c[0].selectbox("질문", qids, index=qids.index("Q5") if "Q5" in qids else 0, key="demo_q")
    exps = ["(전체)"] + sorted({r.get("실험") or "(단독 실행)" for r in rows if r["질문id"] == qid})
    exp = c[1].selectbox("실험", exps, key="demo_exp")
    pool = [r for r in rows if r["질문id"] == qid and (exp == "(전체)" or (r.get("실험") or "(단독 실행)") == exp)]
    setting = c[2].selectbox("설정", ["(전체)"] + sorted({r["설정"] for r in pool}), key="demo_set")
    pool = [r for r in pool if setting == "(전체)" or r["설정"] == setting]
    first_team = next((i for i, x in enumerate(pool) if x["종류"] == "팀" and x["설정"] == "기본"), 0)
    current = c[3].selectbox("실행", pool, index=first_team, format_func=run_label, key="demo_run")
    q_meta = next((x for x in QUESTIONS if x["id"] == qid), None)
    if q_meta:
        st.caption(f"{q_meta['성격']} 질문 — {q_meta['왜_나눌_만한가']}")
    label("RESULT")
    render_run(current)

else:  # 질문 선택
    options = [f"{x['id']} ({x['성격']}) {x['질문']}" for x in QUESTIONS]
    pick = st.selectbox("질문", options, index=4, key="pick_q")
    chosen = QUESTIONS[options.index(pick)]
    st.caption(f"왜 나눌 만한가 — {chosen['왜_나눌_만한가']}")
    if not HAS_KEY:
        st.markdown("<div class='notice'><b>OPENAI_API_KEY 가 없다</b> — 이 질문의 저장된 실행은 "
                    "데모 보기에서 볼 수 있다.</div>", unsafe_allow_html=True)
    sw = switches_panel("pick")
    if st.button("이 질문 돌리기", type="primary", disabled=not HAS_KEY):
        st.session_state["pick_run"] = run_live(chosen["질문"], chosen["id"], sw)
    if not show_last("pick_run"):
        st.markdown("<div class='empty'>질문을 고르고 '이 질문 돌리기'. 저장된 결과만 보려면 데모 보기.</div>",
                    unsafe_allow_html=True)
