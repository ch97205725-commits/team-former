"""지원, 투표, 마감, 명단 초기화, 배정 확정. 모든 함수는 DB 커서(cur)를 받아 한 트랜잭션 안에서 실행됨.
돌려주는 값은 (첫 단어, 안내 문장). 첫 단어는 '승인', '대기', '거절', '막힘' 가운데 하나임."""
from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field

import rules
from rules import Student

WRITE_LOCK = 7_301_2026  # 쓰기 작업을 한 줄로 세우는 advisory lock 번호


def _lock(cur):
    # 지원·투표·마감·확정이 동시에 들어와도 하나씩 처리됨(트랜잭션이 끝나면 자동으로 풀림)
    cur.execute("select pg_advisory_xact_lock(%s)", (WRITE_LOCK,))


# ------------------------------------------------------------------ 상태 읽기
@dataclass
class State:
    students: dict = field(default_factory=dict)      # id -> Student
    by_no: dict = field(default_factory=dict)         # 학번 -> Student
    teams: dict = field(default_factory=dict)         # 팀번호 -> [Student] (자리 순서)
    member_of: dict = field(default_factory=dict)     # student id -> 팀번호
    pending: list = field(default_factory=list)       # [dict(id, team_id, student_id, created_at, votes={voter_id: vote})]
    last_app: dict = field(default_factory=dict)      # student id -> 가장 최근 지원 dict
    closed: bool = False
    confirmed: bool = False

    def unassigned(self):
        return [s for s in self.students.values() if s.id not in self.member_of]


def get_setting(cur, key, default=None):
    cur.execute("select value from tf_settings where key = %s", (key,))
    row = cur.fetchone()
    return row[0] if row else default


def set_setting(cur, key, value):
    cur.execute(
        "insert into tf_settings(key, value) values (%s, %s) "
        "on conflict (key) do update set value = excluded.value",
        (key, value),
    )


def load_state(cur) -> State:
    st = State()
    cur.execute("select student_id, role from tf_student_sub_roles order by student_id, role")
    subs = {}
    for sid, role in cur.fetchall():
        subs.setdefault(sid, []).append(role)
    cur.execute("select id, student_no, name, main_role from tf_students where not is_admin order by student_no")
    for sid, no, name, main in cur.fetchall():
        s = Student(sid, no, name, main, tuple(subs.get(sid, [])))
        st.students[sid] = s
        st.by_no[no] = s
    cur.execute("select id from tf_teams order by id")
    for (tid,) in cur.fetchall():
        st.teams[tid] = []
    cur.execute("select team_id, student_id from tf_members order by team_id, slot_no")
    for tid, sid in cur.fetchall():
        if sid in st.students:
            st.teams[tid].append(st.students[sid])
            st.member_of[sid] = tid
    cur.execute(
        "select id, team_id, student_id, status, reason, created_at from tf_applications order by id"
    )
    apps = {}
    for aid, tid, sid, status, reason, created in cur.fetchall():
        a = dict(id=aid, team_id=tid, student_id=sid, status=status, reason=reason,
                 created_at=created, votes={})
        apps[aid] = a
        st.last_app[sid] = a
        if status == "대기":
            st.pending.append(a)
    if st.pending:
        cur.execute("select application_id, voter_id, vote from tf_votes")
        for aid, vid, vote in cur.fetchall():
            if aid in apps:
                apps[aid]["votes"][vid] = vote
    st.closed = get_setting(cur, "closed") == "1"
    st.confirmed = get_setting(cur, "confirmed") == "1"
    return st


# ------------------------------------------------------------------ 지원
def _add_member(cur, team_no, student_id):
    cur.execute("select slot_no from tf_members where team_id = %s", (team_no,))
    used = {r[0] for r in cur.fetchall()}
    free = [n for n in range(1, rules.MAX_SIZE + 1) if n not in used]
    if not free:
        raise ValueError("팀이 꽉 참")
    # (team_id, slot_no) 기본 키와 student_id 유일성 제약이 있어 동시에 들어와도 한 명만 기록됨
    cur.execute("insert into tf_members(team_id, slot_no, student_id) values (%s, %s, %s)",
                (team_no, free[0], student_id))


def apply(cur, student_id: int, team_no: int):
    _lock(cur)
    st = load_state(cur)
    me = st.students.get(student_id)
    if me is None:
        return "막힘", "학생 정보가 없음"
    if team_no not in st.teams:
        return "막힘", f"팀{team_no}이(가) 없음"
    if st.closed:
        return "막힘", "모집이 마감됨"
    if student_id in st.member_of:
        return "막힘", "이미 팀이 있음"
    if any(a["student_id"] == student_id for a in st.pending):
        return "막힘", "결과를 기다리는 지원이 있음"
    members = st.teams[team_no]
    reason = rules.join_block_reason(members, me)
    if reason:
        return "막힘", reason
    if not members:
        _add_member(cur, team_no, student_id)
        cur.execute(
            "insert into tf_applications(team_id, student_id, status, reason, decided_at) "
            "values (%s, %s, '승인', '빈 팀이라 바로 승인', now())",
            (team_no, student_id),
        )
        return "승인", f"팀{team_no}에 들어감(빈 팀이라 바로 승인)"
    cur.execute(
        "insert into tf_applications(team_id, student_id, status) values (%s, %s, '대기')",
        (team_no, student_id),
    )
    return "대기", f"팀{team_no}에 지원함. 팀원 {len(members)}명의 투표를 기다림"


# ------------------------------------------------------------------ 투표
def _decide(cur, app_id, status, reason):
    cur.execute(
        "update tf_applications set status = %s, reason = %s, decided_at = now() where id = %s",
        (status, reason, app_id),
    )


def _status_of(st: State, applicant: Student, team_no=None):
    a = st.last_app.get(applicant.id)
    if a is None:
        return "막힘", f"{applicant.name}의 지원이 없음"
    why = f"({a['reason']})" if a.get("reason") else ""
    return a["status"], f"{applicant.name}의 팀{a['team_id']} 지원 {why}".rstrip()


def vote(cur, voter_id: int, applicant_no: str, yes: bool):
    _lock(cur)
    st = load_state(cur)
    applicant = st.by_no.get(applicant_no)
    voter = st.students.get(voter_id)
    if applicant is None or voter is None:
        return "막힘", "학생 정보가 없음"
    team_no = st.member_of.get(voter_id)
    app = next((a for a in st.pending
                if a["student_id"] == applicant.id and a["team_id"] == team_no), None)
    if app is None:
        word, msg = _status_of(st, applicant)
        return word, msg + " — 지금 투표할 수 있는 지원이 아님"
    if st.closed:
        return "거절", "모집이 마감됨"
    if voter_id in app["votes"]:
        word = "대기"
        return word, f"이미 투표함(내 투표: {app['votes'][voter_id]})"

    cur.execute("insert into tf_votes(application_id, voter_id, vote) values (%s, %s, %s)",
                (app["id"], voter_id, "찬성" if yes else "거부"))
    if not yes:
        _decide(cur, app["id"], "거절", f"{voter.name}이(가) 거부함")
        return "거절", f"{applicant.name}의 팀{team_no} 지원을 거부함 → 거절"

    app["votes"][voter_id] = "찬성"
    members = st.teams[team_no]
    yes_ids = {v for v, x in app["votes"].items() if x == "찬성"}
    waiting = [m for m in members if m.id not in yes_ids]
    if waiting:
        return "대기", (f"{applicant.name}의 지원에 찬성함. 남은 투표: "
                       + ", ".join(m.name for m in waiting))

    # 모든 팀원이 찬성 → 승인 직전에 규칙을 다시 검사(앞의 지원이 먼저 승인되어 팀이 바뀌었을 수 있음)
    reason = None
    if applicant.id in st.member_of:
        reason = "이미 팀이 있음"
    else:
        reason = rules.join_block_reason(members, applicant)
    if reason:
        _decide(cur, app["id"], "거절", f"승인 직전 검사: {reason}")
        return "거절", f"{applicant.name}의 지원은 승인 직전 검사에서 거절됨: {reason}"
    _add_member(cur, team_no, applicant.id)
    _decide(cur, app["id"], "승인", "모든 팀원이 찬성함")
    return "승인", f"모든 팀원이 찬성하여 {applicant.name}이(가) 팀{team_no}의 팀원이 됨"


# ------------------------------------------------------------------ 마감
def close(cur):
    _lock(cur)
    st = load_state(cur)
    if st.closed:
        return "막힘", "이미 모집을 마감함"
    set_setting(cur, "closed", "1")
    cur.execute(
        "update tf_applications set status = '거절', reason = '모집 마감', decided_at = now() "
        "where status = '대기'"
    )
    rejected = cur.rowcount
    removed = []
    for t, ms in st.teams.items():
        if len(ms) == 1:
            cur.execute("delete from tf_members where student_id = %s", (ms[0].id,))
            removed.append(f"{ms[0].name}(팀{t})")
    msg = f"모집을 마감함. 대기 중이던 지원 {rejected}건 거절"
    if removed:
        msg += ". 1명뿐인 팀에서 빠진 학생: " + ", ".join(removed)
    return "승인", msg


# ------------------------------------------------------------------ 명단 올리기
def parse_roster(data: bytes):
    """BOM 이 붙은 UTF-8 도 읽음(utf-8-sig). 돌려주는 값: [(학번, 이름, 주 역할, [보조 역할])]"""
    text = data.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        raise ValueError("CSV가 비어 있음")
    header = [h.strip() for h in rows[0]]
    if header[:4] != ["student_no", "name", "main_role", "sub_roles"]:
        raise ValueError("첫 줄은 student_no,name,main_role,sub_roles 여야 함")
    out, seen = [], set()
    for i, r in enumerate(rows[1:], start=2):
        if len(r) != 4:
            raise ValueError(f"{i}번째 줄의 열이 {len(r)}개임(4개여야 함)")
        no, name, main, subs = (c.strip() for c in r)
        if not no or not name:
            raise ValueError(f"{i}번째 줄에 학번이나 이름이 없음")
        if no.lower() == "admin" or no in seen:
            raise ValueError(f"{i}번째 줄의 학번 {no}을(를) 쓸 수 없음(중복 또는 admin)")
        seen.add(no)
        if main not in rules.ROLES:
            raise ValueError(f"{i}번째 줄의 주 역할 '{main}'이(가) 6개 역할에 없음")
        sub_list = [x.strip() for x in subs.split(";") if x.strip()]
        if len(sub_list) > 2 or any(x not in rules.ROLES for x in sub_list) or main in sub_list \
                or len(set(sub_list)) != len(sub_list):
            raise ValueError(f"{i}번째 줄의 보조 역할 '{subs}'이(가) 올바르지 않음")
        out.append((no, name, main, sub_list))
    if not out:
        raise ValueError("학생이 한 명도 없음")
    return out


def reset(cur, roster, team_count: int, hash_fn):
    """앱 상태를 초기화함: 지원·투표·팀원·마감 표시를 지우고, 명단 학생과 빈 팀을 만듦. 관리자 계정은 남김."""
    _lock(cur)
    for sql in (
        "delete from tf_votes", "delete from tf_applications", "delete from tf_members",
        "delete from tf_teams", "delete from tf_student_sub_roles",
        "delete from tf_students where not is_admin",
        "delete from tf_settings",
    ):
        cur.execute(sql)
    for n in range(1, team_count + 1):
        cur.execute("insert into tf_teams(id, name) values (%s, %s)", (n, f"팀{n}"))
    for no, name, main, subs in roster:
        cur.execute(
            "insert into tf_students(student_no, name, main_role, password_hash) "
            "values (%s, %s, %s, %s) returning id",
            (no, name, main, hash_fn("1234")),
        )
        sid = cur.fetchone()[0]
        for r in subs:
            cur.execute("insert into tf_student_sub_roles(student_id, role) values (%s, %s)", (sid, r))
    return "승인", f"명단으로 처음 상태를 만듦(학생 {len(roster)}명, 팀 {team_count}개)"


# ------------------------------------------------------------------ 배정안 저장·확정
def save_proposal(cur, proposal: dict, source: str):
    set_setting(cur, "proposal", json.dumps({"assign": proposal, "source": source}, ensure_ascii=False))


def load_proposal(cur):
    raw = get_setting(cur, "proposal")
    return json.loads(raw) if raw else None


def clear_proposal(cur):
    cur.execute("delete from tf_settings where key = 'proposal'")


def confirm(cur):
    _lock(cur)
    st = load_state(cur)
    if not st.closed:
        return "막힘", "모집을 마감한 뒤에 확정할 수 있음"
    prop = load_proposal(cur)
    if prop is None:
        return "막힘", "확정할 배정안이 없음(먼저 AI 배정을 누름)"
    assign = {no: [t] for no, t in prop["assign"].items()}
    problems = rules.validate_assignment(st.teams, st.unassigned(), assign)
    if problems:
        clear_proposal(cur)
        return "막힘", "배정안이 지금 상태와 맞지 않음: " + "; ".join(problems)
    for no, t in prop["assign"].items():
        _add_member(cur, int(t), st.by_no[no].id)
    clear_proposal(cur)
    set_setting(cur, "confirmed", "1")
    return "승인", f"배정을 확정함({len(prop['assign'])}명 배정, 출처: {prop['source']})"


def log_ai(cur, attempt, model, outcome, detail, elapsed):
    cur.execute(
        "insert into tf_ai_logs(attempt, model, outcome, detail, elapsed_sec) values (%s, %s, %s, %s, %s)",
        (attempt, model, outcome, (detail or "")[:2000], round(elapsed, 1)),
    )


def recent_ai_logs(cur, limit=12):
    cur.execute(
        "select created_at, attempt, model, outcome, detail, elapsed_sec "
        "from tf_ai_logs order by id desc limit %s", (limit,))
    return cur.fetchall()
