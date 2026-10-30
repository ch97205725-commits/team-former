-- 팀 편성 앱(Team Former)의 테이블. 모든 테이블 이름은 tf_ 로 시작함.
-- 앱이 시작할 때 이 파일을 그대로 실행함(create ... if not exists 이므로 여러 번 실행해도 안전함).
-- Supabase SQL 편집기에 붙여 넣어 직접 만들어도 같은 결과가 나옴.

-- 학생과 관리자
create table if not exists tf_students (
    id            bigserial primary key,
    student_no    text not null unique,              -- 학번(관리자는 'admin')
    name          text not null,
    main_role     text check (main_role in ('기획자', '웹 프로그래머', '프론트엔드 디자이너',
                                            '테스터', '보고서 작성자', '발표자')),  -- 관리자는 null
    password_hash text not null,                     -- bcrypt 해시(원래 비밀번호는 저장하지 않음)
    is_admin      boolean not null default false,
    check (is_admin or main_role is not null)
);

-- 보조 역할(학생 1명에 0~2행)
create table if not exists tf_student_sub_roles (
    student_id bigint not null references tf_students(id) on delete cascade,
    role       text   not null check (role in ('기획자', '웹 프로그래머', '프론트엔드 디자이너',
                                               '테스터', '보고서 작성자', '발표자')),
    primary key (student_id, role)
);

-- 팀(id 가 곧 팀 번호: 팀1의 id = 1)
create table if not exists tf_teams (
    id   integer primary key check (id >= 1),
    name text not null unique
);

-- 팀원. (team_id, slot_no) 기본 키 = 한 자리에 한 명만(먼저 도착한 학생만 기록됨)
--       student_id 유일   = 한 학생은 한 팀에만
--       slot_no 1~4        = 다섯 번째 자리를 DB가 막음
create table if not exists tf_members (
    team_id    integer not null references tf_teams(id) on delete cascade,
    slot_no    integer not null check (slot_no between 1 and 4),
    student_id bigint  not null unique references tf_students(id) on delete cascade,
    joined_at  timestamptz not null default now(),
    primary key (team_id, slot_no)
);

-- 지원
create table if not exists tf_applications (
    id          bigserial primary key,
    team_id     integer not null references tf_teams(id) on delete cascade,
    student_id  bigint  not null references tf_students(id) on delete cascade,
    status      text    not null check (status in ('대기', '승인', '거절')),
    reason      text,
    created_at  timestamptz not null default now(),
    decided_at  timestamptz
);
-- 한 학생은 결과를 기다리는 지원을 한 번에 하나만 가질 수 있음
create unique index if not exists tf_applications_one_pending
    on tf_applications (student_id) where status = '대기';

-- 투표. 한 사람은 한 지원에 한 번만 투표함(바꿀 수 없음)
create table if not exists tf_votes (
    application_id bigint not null references tf_applications(id) on delete cascade,
    voter_id       bigint not null references tf_students(id) on delete cascade,
    vote           text   not null check (vote in ('찬성', '거부')),
    created_at     timestamptz not null default now(),
    primary key (application_id, voter_id)
);

-- 설정(마감 여부, 배정안 등)
create table if not exists tf_settings (
    key   text primary key,
    value text
);

-- AI 호출 기록(README의 실패 사례 근거로 씀)
create table if not exists tf_ai_logs (
    id          bigserial primary key,
    created_at  timestamptz not null default now(),
    attempt     integer not null,          -- 1, 2, 3차
    model       text    not null,
    outcome     text    not null,          -- 성공, 시간 초과, 오류, 빈 응답, 검사 실패
    detail      text,
    elapsed_sec numeric(7, 1)
);
