-- RubricTrace schema v1. PostgreSQL 15+ (Supabase).
--
-- Written WITHOUT schema qualification on purpose: Supabase runs it in `public`, and the test
-- suite runs the same file inside a throwaway schema via search_path.
--
-- Design rules:
--   * One source of truth for the answer key: exam_questions / question_options / rubric_criteria.
--   * The AI's mark (ai_marks) is never overwritten; a teacher's decision lives in final_marks.
--   * "Needs review" is defined once, in the evaluation_state view. It is never stored.
--   * scripts.status is pipeline state only. Graded / needs-review is derived (script_summary).
--   * Ownership (owner_id -> auth.users) and RLS policies arrive with the auth migration.
--     Until then every table has RLS enabled with NO policies, so the Supabase Data API
--     (anon / authenticated keys) can read nothing. The backend connects as a privileged role.

-- ── Types ────────────────────────────────────────────────────────────────────

create type question_type as enum ('mcq', 'true_false', 'short_answer', 'long_answer');
create type script_status as enum ('uploaded', 'processing', 'processed', 'error');
create type answer_state  as enum ('answered', 'blank', 'not_found');
create type grader_kind   as enum ('deterministic', 'slm', 'llm', 'rule_blank');
create type review_status as enum ('unreviewed', 'teacher_approved');
create type job_kind      as enum ('process', 'grade');
create type job_status    as enum ('queued', 'running', 'done', 'failed', 'cancelled');

create function set_updated_at() returns trigger
language plpgsql as $$
begin
    new.updated_at = now();
    return new;
end
$$;

-- ── Evaluators ───────────────────────────────────────────────────────────────

create table evaluator_configs (
    id                 uuid primary key default gen_random_uuid(),
    name               text not null check (btrim(name) <> ''),
    kind               grader_kind not null check (kind <> 'rule_blank'),
    provider_name      text not null,
    model_name         text not null,
    temperature        numeric(3, 2) not null default 0 check (temperature >= 0),
    max_tokens         integer check (max_tokens > 0),
    fallback_config_id uuid references evaluator_configs (id) on delete set null,
    created_at         timestamptz not null default now(),
    updated_at         timestamptz not null default now(),
    check (fallback_config_id is distinct from id)
);

-- ── Exams and their answer key ───────────────────────────────────────────────

create table exams (
    id               uuid primary key default gen_random_uuid(),
    name             text not null check (btrim(name) <> ''),
    -- Answers graded with confidence below this are shown for review (a question can override).
    review_threshold numeric(3, 2) not null default 0.65
                     check (review_threshold between 0.05 and 1.00),
    -- Marks are awarded in multiples of this (1 = whole marks, 0.5 = half marks).
    mark_step        numeric(4, 2) not null default 1.00 check (mark_step > 0),
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now()
);

-- Question rows keep their id across saves (upsert by (exam_id, question_id)), so grades can
-- reference them with a real foreign key.
create table exam_questions (
    id                  uuid primary key default gen_random_uuid(),
    exam_id             uuid not null references exams (id) on delete cascade,
    question_id         text not null check (btrim(question_id) <> ''),  -- label, e.g. 'Q3'
    question_number     integer not null check (question_number > 0),
    question_type       question_type not null,
    question_text       text not null default '',
    -- Reference answer for descriptive questions. Objective questions are decided by
    -- question_options.is_correct instead.
    golden_answer       text not null default '',
    max_marks           numeric(6, 2) not null check (max_marks > 0),
    review_threshold    numeric(3, 2) check (review_threshold between 0.05 and 1.00),
    evaluator_config_id uuid references evaluator_configs (id) on delete set null,
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),
    unique (exam_id, question_id),
    -- Deferred so questions can be renumbered inside one transaction.
    unique (exam_id, question_number) deferrable initially deferred
);

create table question_options (
    id               uuid primary key default gen_random_uuid(),
    exam_question_id uuid not null references exam_questions (id) on delete cascade,
    option_key       text not null,
    option_text      text not null,
    is_correct       boolean not null default false,
    display_order    integer not null default 0,
    unique (exam_question_id, option_key)
);

create table rubric_criteria (
    id                uuid primary key default gen_random_uuid(),
    exam_question_id  uuid not null references exam_questions (id) on delete cascade,
    name              text not null,
    description       text not null default '',
    max_marks         numeric(6, 2) not null check (max_marks > 0),
    expected_concepts text[] not null default '{}',
    guidance          text not null default '',
    display_order     integer not null default 0
);
create index rubric_criteria_question_idx on rubric_criteria (exam_question_id);

-- ── Students and scripts ─────────────────────────────────────────────────────

create table students (
    id          uuid primary key default gen_random_uuid(),
    external_id text not null unique check (btrim(external_id) <> ''),  -- roll no / dataset id
    name        text not null default '',
    created_at  timestamptz not null default now()
);

create table scripts (
    id          uuid primary key default gen_random_uuid(),
    exam_id     uuid not null references exams (id) on delete cascade,
    student_id  uuid not null references students (id) on delete cascade,
    filename    text not null,
    storage_key text not null,           -- object key in Supabase Storage, never a local path
    file_sha256 text,
    page_count  integer check (page_count >= 0),
    status      script_status not null default 'uploaded',
    error       text,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now(),
    -- One script per student per exam; a re-upload replaces the old one.
    unique (exam_id, student_id),
    check (status = 'error' or error is null)
);
create index scripts_student_idx on scripts (student_id);
create unique index scripts_exam_sha_uniq on scripts (exam_id, file_sha256)
    where file_sha256 is not null;

create table script_pages (
    script_id   uuid not null references scripts (id) on delete cascade,
    page_number integer not null,
    storage_key text not null,
    width       integer,
    height      integer,
    primary key (script_id, page_number)
);

-- What the vision model read. question_id is the label as detected on the page; it is matched
-- to exam_questions through scripts.exam_id.
create table extractions (
    id             uuid primary key default gen_random_uuid(),
    script_id      uuid not null references scripts (id) on delete cascade,
    question_id    text not null,
    question_number integer,
    page_numbers   integer[] not null default '{}',
    extracted_text text not null default '',
    answer_state   answer_state not null default 'answered',
    confidence     double precision check (confidence between 0 and 1),
    model          text,
    prompt_version text,
    flags          text[] not null default '{}',   -- e.g. 'illegible', 'low_confidence'
    created_at     timestamptz not null default now(),
    unique (script_id, question_id),
    check (answer_state = 'answered' or btrim(extracted_text) = '')
);

-- ── Background jobs ──────────────────────────────────────────────────────────

create table jobs (
    id               uuid primary key default gen_random_uuid(),
    exam_id          uuid not null references exams (id) on delete cascade,
    kind             job_kind not null,
    status           job_status not null default 'queued',
    total            integer not null default 0 check (total >= 0),
    done             integer not null default 0 check (done >= 0),
    message          text not null default '',
    error            text,
    cancel_requested boolean not null default false,
    heartbeat_at     timestamptz,
    started_at       timestamptz,
    finished_at      timestamptz,
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now(),
    check (done <= total)
);
create index jobs_exam_created_idx on jobs (exam_id, created_at desc);
-- At most one active job per exam, enforced here instead of by a racy check in code.
create unique index jobs_one_active_per_exam on jobs (exam_id)
    where status in ('queued', 'running');

-- ── Grades ───────────────────────────────────────────────────────────────────

create table evaluations (
    id               uuid primary key default gen_random_uuid(),
    script_id        uuid not null references scripts (id) on delete cascade,
    exam_question_id uuid not null references exam_questions (id) on delete cascade,
    extraction_id    uuid references extractions (id) on delete set null,
    job_id           uuid references jobs (id) on delete set null,
    grader_kind      grader_kind not null,

    -- max_marks is the maximum AT GRADING TIME; changing the key means re-grading.
    max_marks        numeric(6, 2) not null check (max_marks > 0),
    -- ai_marks is immutable once written (a re-grade replaces the whole row).
    ai_marks         numeric(6, 2) not null,
    -- final_marks is what counts: equal to ai_marks until a teacher overrides it.
    final_marks      numeric(6, 2) not null,
    confidence       double precision not null check (confidence between 0 and 1),
    -- Grade-time reasons that do NOT depend on confidence (e.g. 'non-blank answer scored 0').
    -- The confidence-based flag is derived in evaluation_state; never store it here.
    flag_reasons     text[] not null default '{}',

    reasoning        text not null default '',
    evidence         jsonb not null default '[]',
    criteria_scores  jsonb not null default '[]',
    llm_output       jsonb,
    -- Exactly what was graded: question text, golden answer, rubric, max marks, answer text.
    grading_snapshot jsonb not null default '{}',
    model            text,
    prompt_version   text,

    review_status    review_status not null default 'unreviewed',
    reviewed_at      timestamptz,
    teacher_reason   text,

    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now(),

    unique (script_id, exam_question_id),
    check (ai_marks    between 0 and max_marks),
    check (final_marks between 0 and max_marks),
    check (review_status = 'unreviewed' or reviewed_at is not null)
);
create index evaluations_question_idx on evaluations (exam_question_id);
create index evaluations_job_idx on evaluations (job_id);

-- ── updated_at triggers ──────────────────────────────────────────────────────

create trigger evaluator_configs_updated before update on evaluator_configs
    for each row execute function set_updated_at();
create trigger exams_updated before update on exams
    for each row execute function set_updated_at();
create trigger exam_questions_updated before update on exam_questions
    for each row execute function set_updated_at();
create trigger scripts_updated before update on scripts
    for each row execute function set_updated_at();
create trigger jobs_updated before update on jobs
    for each row execute function set_updated_at();
create trigger evaluations_updated before update on evaluations
    for each row execute function set_updated_at();

-- ── Derived state (defined once) ─────────────────────────────────────────────

-- An answer needs review when no teacher has approved it AND either its confidence is below the
-- threshold that applies to it (question override, else exam default) or grading attached a
-- rule-based reason. Changing a threshold therefore never needs a re-grade.
create view evaluation_state with (security_invoker = true) as
select
    ev.*,
    coalesce(q.review_threshold, e.review_threshold) as effective_threshold,
    case
        when ev.review_status = 'teacher_approved' then false
        else ev.confidence < coalesce(q.review_threshold, e.review_threshold)
             or cardinality(ev.flag_reasons) > 0
    end as needs_review
from evaluations ev
join scripts s on s.id = ev.script_id
join exam_questions q on q.id = ev.exam_question_id
join exams e on e.id = s.exam_id;

-- One row per script: pipeline status plus marks and review counts derived from its grades.
create view script_summary with (security_invoker = true) as
select
    s.id          as script_id,
    s.exam_id,
    s.student_id,
    s.filename,
    s.page_count,
    s.error,
    s.created_at,
    s.status      as pipeline_status,
    count(es.id)::int                                   as graded_questions,
    coalesce(sum(es.final_marks), 0)                    as total_awarded,
    coalesce(sum(es.max_marks), 0)                      as total_max,
    (count(*) filter (where es.needs_review))::int      as needs_review_count,
    case
        when s.status in ('processing', 'error') then s.status::text
        when count(es.id) = 0 then s.status::text
        when count(*) filter (where es.needs_review) > 0 then 'needs_review'
        else 'graded'
    end as status
from scripts s
left join evaluation_state es on es.script_id = s.id
group by s.id;

-- ── Row level security ───────────────────────────────────────────────────────

alter table evaluator_configs enable row level security;
alter table exams             enable row level security;
alter table exam_questions    enable row level security;
alter table question_options  enable row level security;
alter table rubric_criteria   enable row level security;
alter table students          enable row level security;
alter table scripts           enable row level security;
alter table script_pages      enable row level security;
alter table extractions       enable row level security;
alter table jobs              enable row level security;
alter table evaluations       enable row level security;

-- Supabase grants new objects to anon / authenticated by default; take that away. Guarded so
-- the file also runs on plain PostgreSQL, where those roles do not exist.
do $$
declare
    r text;
begin
    foreach r in array array['anon', 'authenticated'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on all tables in schema %I from %I', current_schema(), r);
        end if;
    end loop;
end
$$;
