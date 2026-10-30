"""팀 규칙(공고 2.3절)과 배정 검사·탐색. DB와 화면에 의존하지 않는 순수 함수만 둠."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

ROLES = ["기획자", "웹 프로그래머", "프론트엔드 디자이너", "테스터", "보고서 작성자", "발표자"]
PL, WP = "기획자", "웹 프로그래머"
REQUIRED = (PL, WP)
MIN_SIZE, MAX_SIZE = 2, 4


@dataclass(frozen=True)
class Student:
    id: int
    student_no: str
    name: str
    main_role: str
    sub_roles: tuple = field(default_factory=tuple)

    @property
    def capable(self) -> set:
        return {self.main_role, *self.sub_roles}

    def roles_text(self) -> str:
        if self.sub_roles:
            return f"{self.main_role} (보조: {', '.join(self.sub_roles)})"
        return self.main_role


# ---------------------------------------------------------------- 필수 포함 역할
def required_coverage(members) -> int:
    """기획자와 웹 프로그래머를 서로 다른 학생이 맡는다고 할 때, 채울 수 있는 필수 역할 수(0~2)."""
    pl = [m for m in members if PL in m.capable]
    wp = [m for m in members if WP in m.capable]
    for a in pl:
        for b in wp:
            if a.id != b.id:
                return 2
    return 1 if (pl or wp) else 0


def unfilled_required(members) -> int:
    return len(REQUIRED) - required_coverage(members)


def missing_required_roles(members) -> list:
    """아직 못 채운 필수 역할 이름. 둘 다 한 사람만 할 수 있으면 하나만 채운 것으로 봄."""
    cov = required_coverage(members)
    if cov == 2:
        return []
    has_pl = any(PL in m.capable for m in members)
    has_wp = any(WP in m.capable for m in members)
    if cov == 0:
        return [PL, WP]
    # cov == 1
    if has_pl and not has_wp:
        return [WP]
    if has_wp and not has_pl:
        return [PL]
    return ["기획자 또는 웹 프로그래머(한 사람이 둘 다 맡을 수 없음)"]


# ---------------------------------------------------------------- 지원 검사
def join_block_reason(members, applicant: Student, check_required_slots: bool = True):
    """applicant 가 members 팀에 들어갈 수 없으면 이유를, 들어갈 수 있으면 None 을 돌려줌."""
    if len(members) >= MAX_SIZE:
        return "팀이 꽉 참"
    if any(m.main_role == applicant.main_role for m in members):
        return "같은 주 역할의 팀원이 있음"
    if check_required_slots:
        after = list(members) + [applicant]
        free = MAX_SIZE - len(after)
        if unfilled_required(after) > free:
            need = ", ".join(missing_required_roles(after))
            return f"필수 포함 역할을 맡을 학생의 자리가 모자람(남는 자리 {free}개, 아직 필요한 역할: {need})"
    return None


# ---------------------------------------------------------------- 팀 구성 검사
def team_problems(team_no: int, members) -> list:
    probs = []
    n = len(members)
    if n < MIN_SIZE or n > MAX_SIZE:
        probs.append(f"팀{team_no}의 인원이 {n}명임(2~4명이어야 함)")
    seen = {}
    for m in members:
        if m.main_role in seen:
            probs.append(f"팀{team_no}에 주 역할 '{m.main_role}'이 중복됨({seen[m.main_role]}, {m.name})")
        seen[m.main_role] = m.name
    miss = missing_required_roles(members)
    if miss:
        probs.append(f"팀{team_no}에 {', '.join(miss)}을(를) 맡을 학생이 없음")
    return probs


def validate_assignment(teams: dict, unassigned: list, proposal: dict) -> list:
    """공고 2.5절 4항의 다섯 가지 검사.
    teams: {팀번호: [Student,...]} (지금 팀원), unassigned: [Student], proposal: {학번: [팀번호,...]}
    돌려주는 값: 문제 목록(빈 목록이면 통과)."""
    probs = []
    by_no = {s.student_no: s for s in unassigned}
    # 1) 팀이 없는 학생이 빠짐없이 배정됨
    missing = [s.name for s in unassigned if s.student_no not in proposal or not proposal[s.student_no]]
    if missing:
        probs.append("배정되지 않은 학생: " + ", ".join(missing))
    # 2) 한 학생은 한 팀에만
    for no, tlist in proposal.items():
        if no not in by_no:
            probs.append(f"팀이 없는 학생이 아닌 학번이 들어 있음: {no}")
            continue
        if len(set(tlist)) > 1:
            probs.append(f"{by_no[no].name}이(가) 여러 팀({', '.join('팀'+str(t) for t in tlist)})에 배정됨")
        for t in tlist:
            if t not in teams:
                probs.append(f"{by_no[no].name}이(가) 없는 팀(팀{t})에 배정됨")
    # 3~5) 인원, 주 역할 중복, 필수 포함 역할
    final = {t: list(ms) for t, ms in teams.items()}
    for no, tlist in proposal.items():
        if no in by_no and len(set(tlist)) == 1 and tlist[0] in final:
            final[tlist[0]].append(by_no[no])
    for t in sorted(final):
        probs.extend(team_problems(t, final[t]))
    return probs


# ---------------------------------------------------------------- 앱이 스스로 배정(모든 경우 탐색)
def solve(teams: dict, unassigned: list, time_limit: float = 30.0):
    """규칙을 모두 지키는 배정을 백트래킹으로 끝까지 찾음. 찾으면 {학번: 팀번호}, 없으면 None.
    time_limit 를 넘기면 TimeoutError."""
    team_nos = sorted(teams)
    cur = {t: list(teams[t]) for t in team_nos}
    deadline = time.monotonic() + time_limit

    def options(s):
        return [t for t in team_nos
                if len(cur[t]) < MAX_SIZE and all(m.main_role != s.main_role for m in cur[t])]

    # 선택지가 적은 학생부터 넣으면 빨리 끝남
    order = sorted(unassigned, key=lambda s: len(options(s)))
    result = {}

    def feasible_so_far(remaining: int) -> bool:
        need_people = 0
        for t in team_nos:
            free = MAX_SIZE - len(cur[t])
            if unfilled_required(cur[t]) > free:
                return False
            need_people += max(MIN_SIZE - len(cur[t]), unfilled_required(cur[t]), 0)
        return need_people <= remaining

    def rec(i):
        if time.monotonic() > deadline:
            raise TimeoutError
        if not feasible_so_far(len(order) - i):
            return False
        if i == len(order):
            return all(not team_problems(t, cur[t]) for t in team_nos)
        s = order[i]
        for t in options(s):
            cur[t].append(s)
            result[s.student_no] = t
            if rec(i + 1):
                return True
            cur[t].pop()
            del result[s.student_no]
        return False

    return dict(result) if rec(0) else None


def infeasible_reasons(teams: dict, unassigned: list) -> list:
    """규칙을 지키는 배정이 없을 때 관리자에게 보여 줄 이유."""
    reasons = []
    total_free = sum(MAX_SIZE - len(ms) for ms in teams.values())
    if len(unassigned) > total_free:
        reasons.append(f"팀이 없는 학생은 {len(unassigned)}명인데 남은 자리는 {total_free}개뿐임")
    for t in sorted(teams):
        ms = teams[t]
        free = MAX_SIZE - len(ms)
        miss = missing_required_roles(ms)
        if miss and unfilled_required(ms) > free:
            reasons.append(f"팀{t}은(는) 남은 자리 {free}개로 {', '.join(miss)}을(를) 채울 수 없음")
            continue
        # 이 팀에 들어올 수 있는(주 역할이 겹치지 않는) 남은 학생만으로 따져 봄
        pool = [s for s in unassigned if all(m.main_role != s.main_role for m in ms)]
        for role in REQUIRED:
            if not any(role in m.capable for m in ms) and not any(role in s.capable for s in pool):
                reasons.append(f"팀{t}에 {role}을(를) 맡을 학생이 없음")
        if len(ms) < MIN_SIZE and len(ms) + len(pool) < MIN_SIZE:
            reasons.append(f"팀{t}을(를) 2명 이상으로 채울 학생이 모자람")
    if not reasons:
        reasons.append("남은 학생의 역할 조합으로는 모든 팀이 규칙을 동시에 지킬 수 없음(모든 경우를 따져 봄)")
    return reasons
