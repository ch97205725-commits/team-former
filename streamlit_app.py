"""팀 프로젝트 팀 편성 앱(Team Former). 실행: streamlit run streamlit_app.py"""
from __future__ import annotations

import os
from zoneinfo import ZoneInfo

import bcrypt
import streamlit as st
from dotenv import load_dotenv

import ai
import db
import rules
import service

load_dotenv()  # 로컬에서는 .env, 배포한 앱에서는 Streamlit Secrets
KST = ZoneInfo("Asia/Seoul")
REFRESH_SEC = 3  # 다른 사람의 변경을 다시 그리는 간격(5초 이하)

st.set_page_config(page_title="팀 편성", page_icon="🧩", layout="centered")


# ------------------------------------------------------------------ 비밀 값
def secret(name: str):
    """Streamlit Secrets 를 먼저 보고, 없으면 .env(환경 변수)를 봄. 값은 절대 화면에 출력하지 않음."""
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:  # secrets.toml 이 없을 때
        pass
    return os.environ.get(name)


def hash_pw(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=10)).decode()


def check_pw(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except ValueError:
        return False


# ------------------------------------------------------------------ DB 연결(한 번만 만듦)
def _ensure_admin(cur, password: str):
    cur.execute("select id, password_hash from tf_students where student_no = 'admin'")
    row = cur.fetchone()
    if row is None:
        cur.execute(
            "insert into tf_students(student_no, name, main_role, password_hash, is_admin) "
            "values ('admin', '관리자', null, %s, true)", (hash_pw(password),))
    elif not check_pw(password, row[1]):
        cur.execute("update tf_students set password_hash = %s where id = %s", (hash_pw(password), row[0]))


@st.cache_resource(show_spinner="DB에 연결하는 중…")
def get_pool():
    # 실패하면 예외를 내서 캐시에 남지 않게 함(다음 방문 때 다시 시도)
    pool = db.make_pool(secret("DATABASE_URL"))
    db.run(pool, db.ensure_schema)
    db.run(pool, _ensure_admin, secret("ADMIN_PASSWORD"))
    return pool


_missing = [n for n in ("DATABASE_URL", "ADMIN_PASSWORD") if not secret(n)]
if _missing:
    # 값 자체는 보여 주지 않고 '없음'만 알림
    st.error("앱을 시작할 수 없음 — 비밀 값이 없음: " + ", ".join(_missing)
             + ". .env 파일이나 Streamlit Secrets 를 확인하세요.")
    st.stop()
try:
    pool = get_pool()
except Exception as e:  # noqa: BLE001
    # DB 주소의 비밀번호와 사용자 이름은 가리고, 원인을 짐작할 수 있는 안내만 보여 줌
    st.error(f"DB에 연결하지 못함({type(e).__name__}). DATABASE_URL 값과 Supabase 프로젝트가 "
             "일시정지되지 않았는지 확인하세요.")
    st.markdown("**진단** (비밀번호는 가림)")
    for line in db.diagnose(secret("DATABASE_URL"), e):
        st.markdown(f"- {line}")
    st.stop()


def run(fn, *args):
    return db.run(pool, fn, *args)


# ------------------------------------------------------------------ 공통 표시
def show_result(word_msg):
    """결과 안내. 첫 단어는 승인/대기/거절/막힘."""
    if not word_msg:
        return
    word, msg = word_msg
    text = f"{word}: {msg}"
    {"승인": st.success, "대기": st.info, "거절": st.warning, "막힘": st.error}.get(word, st.write)(text)


def team_box(team_no, members, mine=False, extra=None):
    with st.container(key=f"team_{team_no}", border=True):
        title = f"**팀{team_no}** · {len(members)}/4명"
        if mine:
            title += " · 🟢 내 팀"
        st.markdown(title)
        if members:
            for m in members:
                st.markdown(f"- {m.name} — {m.roles_text()}")
        else:
            st.caption("빈 팀")
        miss = rules.missing_required_roles(members)
        if members and miss:
            st.caption("아직 필요한 역할: " + ", ".join(miss))
        if extra:
            extra()


def whoami_bar(user):
    c1, c2 = st.columns([4, 1])
    with c1:
        with st.container(key="whoami"):
            label = "관리자" if user["is_admin"] else "학생"
            st.markdown(f"로그인: **{user['name']}** ({user['student_no']}, {label})")
    with c2:
        if st.button("로그아웃", key="logout"):
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            st.rerun()


# ------------------------------------------------------------------ 로그인
def _find_user(cur, no):
    cur.execute("select id, student_no, name, password_hash, is_admin from tf_students where student_no = %s",
                (no,))
    return cur.fetchone()


def login_page():
    st.title("🧩 팀 프로젝트 팀 편성")
    with st.form("login_form"):
        no = st.text_input("학번 (관리자는 admin)", key="login_student_no")
        pw = st.text_input("비밀번호", type="password", key="login_password")
        submitted = st.form_submit_button("로그인", key="login_submit", type="primary")
    if submitted:
        row = run(_find_user, no.strip())
        if row and check_pw(pw, row[3]):
            st.session_state.user = dict(id=row[0], student_no=row[1], name=row[2], is_admin=row[4])
            st.session_state.msg = None
            st.rerun()
        else:
            st.error("막힘: 학번이나 비밀번호가 틀림")


# ------------------------------------------------------------------ 학생 화면
def on_apply(team_no):
    u = st.session_state.user
    st.session_state.msg = run(service.apply, u["id"], team_no)


def on_vote(applicant_no, yes):
    u = st.session_state.user
    st.session_state.msg = run(service.vote, u["id"], applicant_no, yes)


@st.fragment(run_every=REFRESH_SEC)
def student_live():
    u = st.session_state.user
    S = run(service.load_state)
    me = S.students.get(u["id"])
    with st.container(key="result"):
        show_result(st.session_state.get("msg"))
    if me is None:
        st.warning("명단이 새로 올라와 이 계정이 없어졌음. 로그아웃한 뒤 다시 로그인하세요.")
        return

    my_team = S.member_of.get(me.id)
    my_pending = next((a for a in S.pending if a["student_id"] == me.id), None)
    st.caption(f"내 역할: {me.roles_text()}")
    if S.closed:
        st.info("모집이 마감됨" + (" · 배정이 확정됨" if S.confirmed else ""))
    if my_team:
        st.markdown(f"내 팀: **팀{my_team}**")
    else:
        st.markdown("**아직 팀이 없음**")
    if my_pending:
        st.markdown(f"결과를 기다리는 지원: 팀{my_pending['team_id']}")
    # 이미 팀에 속했거나, 결과를 기다리는 지원이 있거나, 모집이 마감되었으면 지원 단추를 숨김(2.8절)
    can_apply = not (my_team or my_pending or S.closed)

    # 내 팀에 들어온 지원(투표)
    if my_team:
        apps = [a for a in S.pending if a["team_id"] == my_team]
        st.subheader("내 팀에 들어온 지원")
        if not apps:
            st.caption("대기 중인 지원이 없음")
        for a in apps:
            ap = S.students[a["student_id"]]
            with st.container(border=True):
                yes_n = sum(1 for v in a["votes"].values() if v == "찬성")
                st.markdown(f"**{ap.name}** ({ap.student_no}) — {ap.roles_text()}  \n"
                            f"찬성 {yes_n} / 팀원 {len(S.teams[my_team])}명")
                mine = a["votes"].get(me.id)
                if mine:
                    st.markdown(f"내 투표: **{mine}**")
                else:
                    c1, c2 = st.columns(2)
                    c1.button("찬성", key=f"vote_yes_{ap.student_no}", on_click=on_vote,
                              args=(ap.student_no, True))
                    c2.button("거부", key=f"vote_no_{ap.student_no}", on_click=on_vote,
                              args=(ap.student_no, False))

    st.subheader("팀 목록")
    for t, ms in S.teams.items():
        def extra(t=t):
            if can_apply:
                st.button(f"팀{t}에 지원", key=f"apply_{t}", on_click=on_apply, args=(t,))
        team_box(t, ms, mine=(t == my_team), extra=extra)


def student_page():
    whoami_bar(st.session_state.user)
    student_live()


# ------------------------------------------------------------------ 관리자 화면
def _require_admin():
    u = st.session_state.get("user")
    return bool(u and u.get("is_admin"))


def do_assign(ph):
    """마감 뒤 남은 학생 배정: 규칙을 지키는 배정이 있는지 먼저 확인 → 1·2·3차 모델 → 앱이 스스로 배정."""
    S = run(service.load_state)
    if not S.closed:
        ph.error("모집을 마감한 뒤에 배정할 수 있음")
        return "막힘", "모집을 마감한 뒤에 배정할 수 있음"
    teams, unassigned = S.teams, S.unassigned()

    try:
        own = rules.solve(teams, unassigned, time_limit=30)
    except TimeoutError:
        own = "unknown"
    if own is None:
        reasons = rules.infeasible_reasons(teams, unassigned)
        ph.error("규칙을 지키는 배정이 없음 — " + "; ".join(reasons))
        run(service.clear_proposal)
        st.session_state.assign_msg = ("error", "규칙을 지키는 배정이 없음 — " + "; ".join(reasons))
        return "막힘", "규칙을 지키는 배정이 없음: " + "; ".join(reasons)

    proposal, source = None, None
    if not unassigned:
        proposal, source = {}, "배정할 학생 없음"
    else:
        key = secret("OPENROUTER_API_KEY")
        models = ai.get_models(secret)
        prompt = ai.build_prompt(teams, unassigned)
        for i, model in enumerate(models, start=1):
            def tick(sec, i=i, model=model):
                ph.info(f"⏳ {i}차 모델({model})의 응답을 기다리는 중… {int(sec)}초 / {ai.TOTAL_TIMEOUT}초")
            tick(0)
            if not key:
                content, err, el = None, "OPENROUTER_API_KEY 가 없음", 0.0
            else:
                content, err, el = ai.call_model(model, prompt, key, on_tick=tick)
            outcome = "성공"
            if content is not None:
                try:
                    prop = ai.parse_assignments(content)
                    probs = ai.validate(teams, unassigned, prop)
                    if probs:
                        err, outcome = "배정안 검사 실패: " + "; ".join(probs), "검사 실패"
                    else:
                        proposal = {no: ts[0] for no, ts in prop.items()}
                        source = f"{i}차 모델 {model}"
                except (ValueError, TypeError, AttributeError) as e:
                    err, outcome = f"답을 읽을 수 없음: {e}", "형식 오류"
            else:
                outcome = "시간 초과" if "시간 초과" in (err or "") else ("빈 응답" if (err or "").startswith("빈 응답") else "오류")
            run(service.log_ai, i, model, outcome, err or f"{len(proposal or {})}명 배정", el)
            if proposal is not None:
                break
            ph.warning(f"{i}차 모델 실패: {err}. 다음으로 넘어감")
        if proposal is None:
            ph.info("⏳ 3차 모델까지 실패하여 앱이 모든 경우를 따져 배정을 찾는 중…")
            if own == "unknown":
                try:
                    own = rules.solve(teams, unassigned, time_limit=120)
                except TimeoutError:
                    own = None
            if not own:
                ph.error("규칙을 지키는 배정을 찾지 못함")
                return "막힘", "규칙을 지키는 배정을 찾지 못함"
            proposal, source = own, "앱이 직접 찾은 배정(3차 모델까지 실패)"

    run(service.save_proposal, proposal, source)
    done = f"배정안을 만듦 — 출처: {source}. 아래 배정안을 확인하고 '배정안 확정'을 누르세요."
    ph.success(done)
    st.session_state.assign_msg = ("success", done)
    return "승인", f"배정안을 만듦(출처: {source})"


def admin_page():
    whoami_bar(st.session_state.user)
    res_box = st.container(key="result")

    st.subheader("1. 명단 올리기")
    up = st.file_uploader("명단 CSV (UTF-8)", type=["csv"], key="admin_roster_file")
    n = st.number_input("팀 수", min_value=1, max_value=10, value=3, step=1, key="admin_team_count")
    if st.button("명단으로 처음 상태 만들기", key="admin_start", type="primary") and _require_admin():
        if up is None:
            st.session_state.msg = ("막힘", "명단 CSV 파일을 먼저 올리세요")
        else:
            try:
                roster = service.parse_roster(up.getvalue())
                st.session_state.msg = run(service.reset, roster, int(n), hash_pw)
                st.session_state.assign_msg = None
            except ValueError as e:
                st.session_state.msg = ("막힘", f"명단을 읽을 수 없음: {e}")

    st.subheader("2. 모집 마감")
    if st.button("모집 마감", key="admin_close") and _require_admin():
        st.session_state.msg = run(service.close)

    st.subheader("3. AI 배정과 확정")
    clicked = st.button("AI 배정", key="admin_assign")
    assign_box = st.container(key="assign_status")
    ph = assign_box.empty()
    if clicked and _require_admin():
        st.session_state.msg = do_assign(ph)
    else:
        am = st.session_state.get("assign_msg")
        if am:
            getattr(ph, am[0])(am[1])
        else:
            ph.caption("AI 배정을 누르면 진행 상황이 여기에 나타남")

    prop = run(service.load_proposal)
    if prop is not None:
        S = run(service.load_state)
        rows = []
        for no, t in sorted(prop["assign"].items(), key=lambda x: (x[1], x[0])):
            s = S.by_no.get(no)
            if s:
                rows.append({"팀": f"팀{t}", "이름": s.name, "주 역할": s.main_role,
                             "보조 역할": ", ".join(s.sub_roles) or "없음"})
        st.markdown(f"**배정안** (출처: {prop['source']})")
        if rows:
            st.table(rows)
        else:
            st.caption("배정할 학생이 없음")
    if st.button("배정안 확정", key="admin_confirm", type="primary") and _require_admin():
        st.session_state.msg = run(service.confirm)
        st.session_state.assign_msg = None

    with res_box:
        show_result(st.session_state.get("msg"))

    with st.expander("쓰는 모델과 지금 무료인 모델 목록"):
        for i, m in enumerate(ai.get_models(secret), start=1):
            st.markdown(f"- {i}차: `{m}`")
        if st.button("오픈라우터에서 지금 무료인 모델 불러오기", key="list_free_models"):
            try:
                free = ai.list_free_models()
                st.caption(f"무료 텍스트 모델 {len(free)}개(문맥 길이가 긴 순). "
                           "이름을 그대로 복사해 Secrets 의 OPENROUTER_MODEL_1~3 에 넣으세요.")
                st.code("\n".join(f"{mid}    ({ctx:,} tokens)" for mid, ctx in free) or "(없음)",
                        language=None)
            except Exception as e:  # noqa: BLE001
                st.error(f"목록을 불러오지 못함({type(e).__name__})")

    with st.expander("AI 호출 기록(최근 12건)"):
        logs = run(service.recent_ai_logs)
        if not logs:
            st.caption("아직 없음")
        for created, attempt, model, outcome, detail, el in logs:
            t = created.astimezone(KST).strftime("%m-%d %H:%M:%S")
            st.markdown(f"- {t} · {attempt}차 `{model}` · **{outcome}** · {el}초 — {detail}")

    admin_live()


@st.fragment(run_every=REFRESH_SEC)
def admin_live():
    S = run(service.load_state)
    st.subheader("현재 상태")
    st.caption(("모집 마감됨" if S.closed else "모집 중") + (" · 배정 확정됨" if S.confirmed else "")
               + f" · 학생 {len(S.students)}명 · 대기 중인 지원 {len(S.pending)}건")
    with st.container(key="unassigned", border=True):
        st.markdown("**팀이 없는 학생**")
        un = S.unassigned()
        if un:
            for s in un:
                st.markdown(f"- {s.name} — {s.roles_text()}")
        else:
            st.caption("없음")
    for t, ms in S.teams.items():
        team_box(t, ms)


# ------------------------------------------------------------------ 시작
user = st.session_state.get("user")
if not user:
    login_page()
elif user["is_admin"]:
    admin_page()
else:
    student_page()
