# my-deep-researcher — 노벨 문학상 딥리서처

한국어 위키백과의 노벨 문학상 수상자 121명과 대표작 문서를 코퍼스로 삼아,
코디네이터가 목차를 짜고 서브에이전트들이 구역을 나눠 읽은 뒤 장문 보고서를 쓰는 LangGraph 파이프라인.

> 모두의연구소 「에이전트 팀 꾸리기」 9장 프로젝트. 설계와 실험 결과는 [REPORT.md](REPORT.md).

## 구조

```
data/            corpus.json (docs + links) · questions.json
config.json      도메인에 묶인 값 (절수 · 절예산 · 바퀴 상한 · 역할 명단)
collect_corpus.py 위키백과 코퍼스 수집
graph.py         기획 → 배치 → 서브에이전트 → 점검 → 종합
metrics.py       지표 — 정답표를 쓰지 않는다
ablation.py      스위치를 하나씩 끄고 재는 실험
baseline.py      혼자 하는 대조군 (예산을 맞춘다)
app.py           데모
output/          runs.jsonl · ablation.json · reports/
```

## 실행

```bash
python3.14 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # OPENAI_API_KEY 채우기
```

_(단계별 실행 방법은 구현하면서 채운다)_

## 자료 출처

코퍼스는 [한국어 위키백과](https://ko.wikipedia.org) 문서를 MediaWiki API 로 받은 것이며
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/deed.ko) 을 따른다.
