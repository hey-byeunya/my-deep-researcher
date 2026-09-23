"""pytest 설정 — 저장소 뿌리(graph · metrics …)와 tests/(fakes)를 import 경로에 넣는다.

pytest.ini 에 두던 것을 여기로 옮겼다(pytest.ini 는 저장소에 넣지 않는다). 이 파일이 있으면
`.venv/bin/pytest -q` 를 저장소 어디서 부르든 같은 모듈을 찾는다.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for p in (HERE.parent, HERE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
