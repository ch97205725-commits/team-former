"""공고 부록의 연습용 시나리오 3개를 service.py 로 그대로 실행해 결과 안내의 첫 단어를 확인함.
로컬 PostgreSQL(127.0.0.1:5433, trust 인증)이 있어야 함. 실행: python tests/run_scenarios.py"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import minipg  # noqa: E402
import rules  # noqa: E402
import service  # noqa: E402

DATA = ROOT / "tests" / "data"
conn = minipg.Connection()
FAIL = []


def run(fn, *args):
    try:
        with conn.cursor() as cur:
            out = fn(cur, *args)
        conn.commit()
        return out
    except Exception:
        conn.rollback()
        raise


def setup():
    run(lambda cur: cur.execute(";".join(f"drop table if exists {t} cascade" for t in
        ["tf_votes", "tf_applications", "tf_members", "tf_teams", "tf_student_sub_roles",
         "tf_students", "tf_settings", "tf_ai_logs"])))
    run(lambda cur: cur.execute((ROOT / "schema.sql").read_text(encoding="utf-8")))
    run(lambda cur: cur.execute(
        "insert into tf_students(student_no,name,password_hash,is_admin) values ('admin','관리자','x',true)"))


def sid(no):
    return run(service.load_state).by_no[no].id


def check(label, got, expect):
    word = got[0]
    ok = word == expect
    print(f"  {'OK ' if ok else 'XX '} {label:<55} 기대 {expect} / 결과 {word}: {got[1]}")
    if not ok:
        FAIL.append(label)


def scenario(csv_name, teams, steps, prefix):
    print(f"\n=== {csv_name} (팀 {teams}개) ===")
    roster = service.parse_roster((DATA / csv_name).read_bytes())
    print(" ", run(service.reset, roster, teams, lambda pw: "hash-" + pw))
    no = lambda n: f"{prefix}{n:02d}"  # 학생n의 학번
    for st in steps:
        kind = st[0]
        if kind == "apply":
            _, who, team, expect = st
            check(f"학생{who}이(가) 팀{team}에 지원", run(service.apply, sid(no(who)), team), expect)
        elif kind == "vote":
            _, voter, applicant, yes, expect = st
            check(f"학생{voter}이(가) 학생{applicant}에 {'찬성' if yes else '거부'}",
                  run(service.vote, sid(no(voter)), no(applicant), yes), expect)
        elif kind == "close":
            check("관리자가 마감", run(service.close), "승인")
            S = run(service.load_state)
            print("     팀이 없는 학생:", ", ".join(s.name for s in S.unassigned()))
    # 배정: AI 대신 앱이 직접 찾은 배정(3차까지 실패한 경우)
    S = run(service.load_state)
    prop = rules.solve(S.teams, S.unassigned())
    if prop is None:
        print("  XX 규칙을 지키는 배정이 없음:", rules.infeasible_reasons(S.teams, S.unassigned()))
        FAIL.append(csv_name + " 배정")
        return
    probs = rules.validate_assignment(S.teams, S.unassigned(), {k: [v] for k, v in prop.items()})
    print("  배정안:", prop, "검사:", probs or "통과")
    run(service.save_proposal, prop, "시험")
    check("관리자가 확정", run(service.confirm), "승인")
    S = run(service.load_state)
    bad = [p for t, ms in S.teams.items() for p in rules.team_problems(t, ms)]
    print("  확정 뒤:", {f"팀{t}": [m.name for m in ms] for t, ms in S.teams.items()})
    print("  최종 규칙 검사:", bad or "통과", "/ 팀 없는 학생:", [s.name for s in S.unassigned()] or "없음")
    if bad or S.unassigned():
        FAIL.append(csv_name + " 최종")


setup()
A, V = "apply", "vote"
scenario("쉬움.csv", 3, [
    (A, 1, 1, "승인"), (A, 2, 2, "승인"), (A, 3, 3, "승인"),
    (A, 4, 1, "대기"), (V, 1, 4, True, "승인"),
    (A, 5, 2, "대기"), (V, 2, 5, True, "승인"),
    (A, 7, 1, "대기"), (V, 1, 7, True, "대기"), (V, 4, 7, False, "거절"),
    (A, 6, 3, "대기"), (V, 3, 6, True, "승인"),
    ("close",),
], "20261")
scenario("중간.csv", 4, [
    (A, 1, 1, "승인"),
    (A, 5, 1, "대기"), (V, 1, 5, True, "승인"),
    (A, 2, 1, "막힘"),
    (A, 6, 2, "승인"),
    (A, 9, 2, "대기"), (V, 6, 9, True, "승인"),
    (A, 10, 2, "대기"), (V, 6, 10, True, "대기"), (V, 9, 10, False, "거절"),
    (A, 3, 3, "승인"), (A, 11, 4, "승인"),
    (A, 12, 4, "대기"), (A, 12, 3, "막힘"),
    ("close",),
    (A, 2, 3, "막힘"),
], "20262")
scenario("어려움.csv", 4, [
    (A, 1, 1, "승인"),
    (A, 4, 1, "대기"), (V, 1, 4, True, "승인"),
    (A, 7, 1, "대기"), (V, 1, 7, True, "대기"), (V, 4, 7, True, "승인"),
    (A, 2, 2, "승인"),
    (A, 8, 2, "대기"), (V, 2, 8, True, "승인"),
    (A, 5, 2, "대기"), (A, 6, 2, "대기"),
    (V, 2, 5, True, "대기"), (V, 8, 5, True, "승인"),
    (V, 2, 6, True, "대기"), (V, 8, 6, True, "대기"), (V, 5, 6, True, "거절"),
    (A, 11, 3, "승인"),
    (A, 15, 3, "대기"), (V, 11, 15, True, "승인"),
    (A, 14, 3, "막힘"),
    (A, 10, 4, "승인"),
    (A, 13, 1, "대기"), (V, 1, 13, True, "대기"),
    ("close",),
], "20263")

print("\n결과:", "모두 통과" if not FAIL else f"실패 {len(FAIL)}건: {FAIL}")
sys.exit(1 if FAIL else 0)
