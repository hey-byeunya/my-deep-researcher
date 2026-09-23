# 코드 리뷰 — my-deep-researcher (2026-09-23)

> 범위: 작업 트리 전체 + `main` 대비 미커밋 변경분. **코드는 수정하지 않고 읽기만 했다.**
> 방법: 정독(graph/baseline/ablation/metrics/compare/app/collect/check_seeds/tests) + `git diff HEAD` + `.venv/bin/pytest -q` (74 passed) 확인.
> 저장: 본 파일 신규 생성. 기존 파일은 손대지 않음.

## TL;DR

- 전체 설계는 탄탄하다. 기획→배치→서브에이전트→점검→종합→측정 분리, 예산을 글자 수로 잰 것, 인용을 코드가 검사하는 구조(`graph.py:560`, `graph.py:614`), 대조군 예산을 짝 팀 실행 실측에 묶은 것(`baseline.py:151`)은 공정한 비교를 위한 좋은 선택이다.
- 미커밋 변경분은 대체로 정방향이다. 중복 함수 제거(`baseline.py`), 미사용 import 제거, 테마 통일(`compare.py`), 실험 기본값 `ablation-2` 전환은 타당하다.
- 지금 당장 고장 나는 항목은 없으나(테스트 74개 통과), **JSON 파싱 탐욕 매칭, 지표/그래프 간 제목 해소 불일치, 단일 문자 이름 매칭, 캐시 키·파일명 충돌 가능성**은 후속 작업에서 손보는 것을 권장한다.
- 보안상 민감정보 노출은 없다. HTML 이스케이프도 대체로 잘 되어 있다.

## 검증한 것

- `pytest -q`: 74 passed (2026-09-23 실행).
- `git status`: 수정 19건(코드 7 + 테스트 3 + output/README/ignore), 삭제된 리포트 6건, 신규 10건(`app.py`, `.streamlit/`, `make_screenshots.py`, 스크린샷 5, 리포트 3, `tests/test_app.py`).
- `.env.example`은 placeholder만 있음. `.gitignore`에서 `.env` 제외 확인.

## 잘된 점

- `graph.py:186`, `graph.py:199`: 예산 배분·목차 검증을 코드가 담당하고 교정 기록을 남긴다. 모델 환각(없는 문서·겹침·형식 절)을 운에 맡기지 않는다.
- `graph.py:577`, `graph.py:630`: `researcher()` 단독 호출 가능, `keep_new()`로 2바퀴 원고 무조건 덮어쓰기 방지.
- `graph.py:734`, `graph.py:102`: 저장 락 + LLM 지연 생성 락. 병렬 실행을 의식했다.
- `baseline.py:88-98`: 대조군이 멈추면 재질문→단어 겹침 대체(`graph.closest`)로 예산을 끝까지 쓰게 한다. 수업 코드의 가나다순 첫 문서 편향을 피한 것도 명시했다.
- `metrics.py:1-10`, `compare.py:1-9`: 정답표·판정 모델 없이 신호/경보 분리, 최종 판단은 사람이 나란히 읽는다. 지표 오용 방지 문구가 코드·README·데모에 일관되게 있다.
- `collect_corpus.py:10-19`, `check_seeds.py:10-12`: 수집 기준·예외를 docstring에 적고, SEEDS 자동 수정 금지(비교만)를 못 박았다. 코퍼스 고정(`--refresh`시에만)도 재현성에 좋다.
- `tests/fakes.py`: 단계별 가짜 LLM이 잘 짜여 있다. 네트워크 없이 파이프라인 전 구간을 돌린다.
- `app.py:149`, `compare.py:42`: `html.escape` 후 칩/태그 삽입 순서가 올바르다.

## 미커밋 변경분 리뷰 (HEAD 대비)

- `.gitignore`: 제출물(output·data·docs·테스트·테마 추적) vs 제외(.env, .venv, .cache, 에디터, playwright 부산물) 구분이 명확해졌다. `.env.*` + `!.env.example` 순서도 맞다.
- `ablation.py`: 기본 exp `ablation-1`→`ablation-2`, 기본 settings에 `배정끔+재촉` 포함(8개). README의 "8개×3회=72번"과 일치. `Path` 미사용 import 제거. 정방향.
- `baseline.py`: 자가 `closest()` 제거→`graph.closest` 재사용. 중복 제거, 정방향. `import re` 제거도 맞다(본 파일 내 `re` 사용 없음).
- `graph.py`: `initial_state()` 추출 + `stream()` 추가(데모용), CLI 플래그를 켜짐/꺼짐 스위치 모두 대응(`--X`/`--no-X`), 라벨 `끔-`→`바꿈-`. 모두 정방향. docstring에 변경점 6가지 명시도 좋다.
- `compare.py`: 라이트/다크 분기 CSS→터미널 콘솔 토큰으로 통일, `Path` 미사용 제거. 정방향. 단 `app.py`와 CSS 중복은 남는다(아래 제안).
- `config.json`: 절예산·카드 설명을 실제 규칙(혼자=짝 실측, 카드=연도순)과 일치시킴. 정방향.
- `tests/`: `test_ablation.py`의 불필요한 `fake` 변수·주석 정리, `test_baseline.py`를 `graph.closest`로 전환, `test_plan.py`의 미사용 `pytest` 제거. 정방향.
- 주의: `output/reports/` 구 리포트 6건 삭제 + 신 리포트 3건 untracked, `runs.jsonl` 수정. 실험 재현용이라면 삭제보다 보관(또는 별도 아카이브)을 권장. `ablation.json` 2줄 변경이 요약 재생성 결과인지 커밋 메시지에 남기면 좋다.

## 이슈 (심각도순, 수정은 안 함 — 제안만)

### High

1. `graph.py:134-140` `jload()` 탐욕 매칭
   - `re.search(r"\[.*\]" / r"\{.*\}", raw, re.S)`는 첫 `[`부터 마지막 `]`까지 통째로 잡는다. 모델이 설명+JSON 2개를 내놓으면 `json.loads` 실패→default로 떨어진다.
   - 제안: non-greedy(`\[.*?\]`) + 밸런스 파싱, 또는 ` ```json ` 펜스 우선 추출. `tests/test_plan.py`에 깨진 응답 케이스 추가.

2. `graph.py:175-184` `resolve_title()` vs `metrics.py:56-61` `_resolve()` 불일치
   - graph는 따옴표·`«»`·연도 suffix 제거까지 하지만 metrics는 괄호 제거만 한다. `render()`는 LLM 원문 근거를 그대로 `«»`에 넣으므로, 해소 실패분이 `근거율`과 `허위인용`에서 다르게 셀 수 있다.
   - 제안: 해소 함수를 한 곳(`graph.resolve_title`)으로 통일하고 metrics에서 재사용. 불일치 케이스 테스트 추가.

3. `compare.py:67-70` `women_covered()` 단일 토큰 매칭
   - `split()[-1] in text`라서 `벅`(펄 S. 벅), `한강`(강 이름과 충돌) 등이 과다 검출된다. Q5 보조 숫자의 신뢰도가 떨어진다.
   - 제안: 괄호 제거한 전체 이름 기준 매칭. 단일 글자 성은 전체 이름(`펄 S. 벅`)으로만 센다. `len(WOMEN)`을 하드코딩 `18` 대신 사용(`compare.py:77`).

### Medium

4. `app.py:137-142` `load_runs(mtime)` 캐시 키가 mtime 단일값
   - 같은 mtime 틱 안에 `runs.jsonl`이 append되면 stale을 보여준다. ablation 병렬 저장 시 가능성은 낮지만 0은 아니다.
   - 제안: `(mtime, size)` 튜플을 키로.

5. `graph.py:737-758` `save_run()` 파일명 충돌 (초 단위 stamp)
   - `stamp = %Y%m%d-%H%M%S`라서 같은 초에 같은 qid·설정·반복이 겹치면 덮어쓴다. 현재 스레드 분할(질문별)에서는 qid가 달라 안전하지만, 재시도·수동 2회 실행 시 위험하다.
   - 제안: `%f` 추가 또는 `uuid4[:6]` suffix. 기존 파서(`baseline.pair_run`, `ablation.done_key`)는 id 전체 매칭이라 영향 없다.

6. `graph.py:106-121` `_llm` 공유 객체의 스레드 안전
   - 지연 생성은 락으로 막았지만 이후 `invoke`는 여러 스레드(LangGraph 병렬 researcher + ablation `ThreadPoolExecutor`)에서 동시 호출된다. `ChatOpenAI`는 대체로 안전하나 SDK 보장 사항은 아니다.
   - 제안: 현상 없으면 유지하되, 429 급증 시 호출 풀/세마포어 또는 프로세스 분리 고려를 주석에 남긴다. `TRANSIENT`의 `type(e).__name__` 부분 매칭도 SDK 개명 시 깨지므로 가능하면 예외 클래스 직접 import.

7. `baseline.py:100-101` `costs[-1]` 변이에 의존
   - `read_chunk`가 정확히 1건을 append한다는 암묵적 가정에 기대어 `costs[-1]["누가"]="코디"`로 바꾼다. `read_chunk`이 바뀌면 깨진다.
   - 제안: `read_chunk`에 `who` 인자 추가 또는 반환된 cost를 명시적으로 받아 수정.

8. `metrics.py:100` `c["단계"].startswith("기획")` 직접 접근
   - `cost` 항목에 `단계`가 없거나 None이면 KeyError/AttributeError. 현재는 전부 있지만 방어적으로 `c.get("단계","").startswith(...)` 권장.

9. `metrics.py:40-49` `sentences()` 분할 규칙
   - `(?<=[.!?])\s+`라서 마침표 뒤 공백 없는 한국어 문장(`했다.다음`)은 안 쪼개진다. `근거율` 분모가 달라진다. 규칙 변경 시 `tests/test_metrics.py` 동반 수정 주석은 잘 되어 있다.
   - 제안: 현 규칙 유지한다면 README/주석에 한계 명시, 또는 `\s*` + 길이 필터 조합 검토.

10. `ablation.py:77-79` `load_runs()`를 설정마다 재독회
    - 72회 규모라 문제없지만 O(n²) 읽기다. `run_question` 진입 시 1회 읽고 메모리에서 갱신하는 구조가 깔끔하다.

### Low

11. `app.py:189`, `app.py:199-202`: 사이드바 `토큰 "36만"`·`배정 끄면 22%/38%` 하드코딩. 코퍼스·ablation 결과 바뀌면 stale. 가능하면 `graph.DOCS` 길이·`ablation.json`에서 계산.
12. `compare.py:138-165` `main()`에 입력 파일 부재 처리 없음. `runs.jsonl` 없으면 `FileNotFoundError`. `baseline.pair_run`처럼 안내 후 `sys.exit` 권장. 빈 매칭 시 빈 페이지 대신 안내 문구 권장.
13. `collect_corpus.py:84` `COMMON_CAT`의 `운동$`이 `독립운동·여성운동` 같은 비문학 분류까지 받는다. 현재 `COMMON_MIN_SEEDS=5` + 문학 필터와 사람이 결과(`commons`)를 로그로 확인하므로 실해는 작다. 정규식 취지 주석 보강 정도면 충분.
14. `check_seeds.py:39-41`, `check_seeds.py:121-123`: 빈 페이지 조기 return 시 부분 목록을 조용히 확정한다. 경고 로그 권장. `award_years()`는 `result["공식ID_의심"]` 직접 접근이라 단독 호출 시 KeyError. `.get(..., [])` 권장.
15. `graph.py:385` `MIN_READ=300` 때문에 잔여 예산이 300 미만이면 루프 탈출한다. `예산소진율`이 구조적으로 100%에 못 미칠 수 있다. 의도된 절충(짧은 읽기 방지)이나, 지표 해석 시 주석이 있으면 좋다.
16. `graph.py:473-481` `explore()` 큐 우선순위(배정→이어읽기→후보)는 합리적이나, 긴 문서 1건에 예산이 묶이는 것을 막기 위해 한 조각씩 돌아가며 읽는다는 주석이 있다. `cap=2500`과 `MIN_READ` 조합에서 담당 6건 모두 긴 문서면 2바퀴까지 가도 뒤쪽을 못 읽는다. 현재 `최대바퀴=2`와 함께 문서화되어 있어 문제없음. 변경 시 재측정 필요.
17. `app.py:353-359` `show_last()`가 렌더+조회를 겸한다. 동작은 맞으나 `if not show_last(...):` 형태는 읽기 어렵다. `get_last()` + `render` 분리 정도면 충분.
18. CSS 중복(`app.py:26-127` vs `compare.py:90-123`, `.streamlit/config.toml`): 이미 한 번 drift됐다가 이번 diff에서 통일됐다. 다음 drift 방지를 위해 토큰을 단일 소스(예: `theme.py` 또는 css 파일)로 묶는 것을 권장.

## 파일별 한 줄 평

- `graph.py:1-834`: 핵심. 검증·재시도·비용 추적이 촘촘하다. 위 High 1·2, Medium 5·6·8만 보면 된다.
- `baseline.py:1-170`: 공정성 서술이 코드와 일치한다. Medium 7 외에는 깨끗하다.
- `ablation.py:1-155`: 재개 가능·요약 분리·표준편차 병기가 좋다. 설정 순서(기본 우선)도 대조군 예산 의존성과 정합.
- `metrics.py:1-142`: 신호/경보 분리·LLM 불사용이 강점. High 2, Medium 8·9만 확인.
- `compare.py:1-169`: 사람 판단 중심 철학이 명확. High 3, Low 12만 확인.
- `app.py:1-419`: 3모드·진행 로그·탭 구성이 좋다. Medium 4, Low 11·17 정도.
- `collect_corpus.py:1-394`: 판정 함수가 순수 함수로 분리돼 테스트 가능하다. Low 13 정도.
- `check_seeds.py:1-161`: 비교만·사람 확인 원칙이 좋다. Low 14 정도.
- `config.json`: 설명이 실제 규칙과 동기화됐다.이번 diff 방향이 맞다.
- `tests/`: 커버리지 의도가 좋다. `jload` 탐욕·`women_covered`·`sentences` 경계 케이스만 보강하면 된다.
- `make_screenshots.py`, `.streamlit/config.toml`: 캡처 재현 절차·테마 토큰이 문서화돼 있다. playwright를 requirements에서 제외한 것도 맞다(캡처 전용).

## 다음에 할 일 (우선순위, 코드는 안 고쳤음)

1. `jload` non-greedy/펜스 우선 + 테스트.
2. 제목 해소 단일화(`graph.resolve_title` 재사용).
3. `women_covered` 전체 이름 매칭 + `len(WOMEN)`.
4. `load_runs` 키 `(mtime, size)`, `save_run` stamp에 `%f`/난수.
5. `_llm` 동시 호출·`TRANSIENT` 매칭 주석 보강(또는 예외 클래스 import).
6. CSS 토큰 단일 소스화.
7. 삭제된 구 리포트 6건의 보관 여부 결정(아카이브 vs 삭제 유지).

## 부록: 명령

- 테스트: `.venv/bin/pytest -q` → 74 passed.
- diff 확인: `git diff HEAD --stat`, `git diff HEAD -- .gitignore config.json graph.py ablation.py baseline.py compare.py tests/`.
