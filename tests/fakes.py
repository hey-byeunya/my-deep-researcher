"""네트워크 없이 파이프라인을 돌리는 가짜 모델. 시스템 프롬프트를 보고 어느 단계인지 알아챈다."""
import json
import re


class FakeLLM:
    """단계별 응답을 정해 두고, 받은 호출을 모두 기록한다.

    plan    : 기획 응답(dict) 또는 그 목록(재기획용)
    picks   : 다음문서 단계에서 차례로 돌려줄 제목들 (다 쓰면 null)
    write   : 절 이름 -> 집필 응답(dict) 또는 그 목록(바퀴마다). 없으면 읽은 문서를 인용한 기본 원고
    """

    def __init__(self, plan=None, picks=(), write=None):
        self.plans = plan if isinstance(plan, list) else [plan or {"목차": []}]
        self.picks = list(picks)
        self.writes = {k: (v if isinstance(v, list) else [v]) for k, v in (write or {}).items()}
        self.calls = []

    def __call__(self, messages):
        system, user = messages[0]["content"], messages[1]["content"]
        self.calls.append((system, user))
        if "코디네이터" in system and "목차" in system:
            return json.dumps(self.plans.pop(0) if len(self.plans) > 1 else self.plans[0], ensure_ascii=False)
        if "다음에 읽을 문서" in system:
            return json.dumps({"문서": self.picks.pop(0) if self.picks else None}, ensure_ascii=False)
        if "간추린다" in system:
            doc = re.search(r"\[문서: ([^\]·]+?)(?: ·|\])", user).group(1).strip()
            return f"{doc} 에 관한 요약."
        if "한 절을 쓴다" in system:
            section = re.search(r"\[맡은 절\] (.+)", user).group(1).strip()
            if section in self.writes and self.writes[section]:
                w = self.writes[section]
                return json.dumps(w.pop(0) if len(w) > 1 else w[0], ensure_ascii=False)
            docs = re.findall(r"\[자료: ([^\]]+)\]", user)
            body = " ".join(f"{d} 에 관한 문장이다 «{d}»." for d in docs) or "자료가 없다."
            return json.dumps({"본문": body * 3, "충분": True, "부족": ""}, ensure_ascii=False)
        raise AssertionError(f"모르는 단계: {system[:40]}")

    def stage_count(self, key):
        return sum(key in s for s, _ in self.calls)
