"""네트워크 없이 파이프라인을 돌리는 가짜 모델. 시스템 프롬프트를 보고 어느 단계인지 알아챈다."""
import json
import re


class FakeLLM:
    """단계별 응답을 정해 두고, 받은 호출을 모두 기록한다.

    plan    : 기획 응답(dict) 또는 그 목록(재기획용)
    picks   : 다음문서 단계에서 차례로 돌려줄 제목들 (다 쓰면 null)
    write   : 절 이름 -> 집필 응답(dict) 또는 그 목록(바퀴마다). 없으면 읽은 문서를 인용한 기본 원고
    """

    def __init__(self, plan=None, picks=(), write=None, solo_start=()):
        self.solo_start = list(solo_start)
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
            sents = [{"글": f"{d} 에 관한 문장이다.", "근거": [d]} for d in docs] * 3
            return json.dumps({"문장": sents or [{"글": "자료가 없다.", "근거": []}], "충분": True, "부족": ""},
                              ensure_ascii=False)
        if "먼저 읽을 문서" in system:                     # 대조군: 카드를 보고 읽을 목록
            return json.dumps({"읽을문서": list(self.solo_start)}, ensure_ascii=False)
        if "혼자 정리하는 필자" in system:                  # 대조군: 보고서 전체
            docs = re.findall(r"\[자료: ([^\]]+)\]", user)
            n = int(re.search(r"절은 (\d+)개", system).group(1))
            parts = [{"제목": f"절{i}", "문장": [{"글": f"{d} 이야기다.", "근거": [d]} for d in docs] +
                      [{"글": "읽지 않은 문서를 댄다.", "근거": ["토니 모리슨"]}]} for i in range(n)]
            return json.dumps({"제목": "혼자 쓴 보고서", "머리말": "연다.", "절": parts, "맺음말": "닫는다."},
                              ensure_ascii=False)
        if "편집자" in system:
            return json.dumps({"머리말": "이 보고서는 여러 절로 나뉜다.", "맺음말": "이상으로 마친다."},
                              ensure_ascii=False)
        raise AssertionError(f"모르는 단계: {system[:40]}")

    def stage_count(self, key):
        return sum(key in s for s, _ in self.calls)
