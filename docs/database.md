# Database

PostgreSQL (Supabase). The whole schema is `supabase/migrations/*.sql`; the application never
creates or alters tables, and `Database.check_schema()` fails fast at startup if the migrations
have not been applied.

## Tables

| Table | Holds |
|---|---|
| `exams` | name, `review_threshold` (default 0.65), `mark_step` (default 1 = whole marks) |
| `exam_questions` | the answer key: type, text, reference answer, max marks, optional per-question threshold and evaluator. Unique on `(exam_id, question_id)`; the display order is deferred-unique so questions can be renumbered in one transaction |
| `question_options` | options of MCQ / true-false questions; exactly one is correct |
| `rubric_criteria` | criteria of open-ended questions; their marks add up to the question's max marks |
| `evaluator_configs` | named model configurations (kind, provider, model, fallback) |
| `students` | `external_id` (roll number or dataset id) and name; the UUID key stays internal |
| `scripts` | one per student per exam; `storage_key`, `file_sha256`, pipeline `status` |
| `script_pages` | per-page storage keys (reserved; not written yet) |
| `extractions` | what the vision model read: text, `page_numbers`, `answer_state` (`answered` / `blank` / `not_found`), confidence, model, flags |
| `evaluations` | one grade per (script, question): see below |
| `jobs` | background processing / grading runs with progress and a heartbeat |

Statuses and types are Postgres enums (`question_type`, `script_status`, `answer_state`,
`grader_kind`, `review_status`, `job_kind`, `job_status`), so a typo is rejected by the database.

## Grades

An `evaluations` row keeps the AI's result and the teacher's decision apart:

- `ai_marks`: what the model awarded. Never changed after grading.
- `final_marks`: what counts. Equal to `ai_marks` until a teacher overrides it.
- `review_status`: `unreviewed` or `teacher_approved`, with `reviewed_at` and `teacher_reason`.
- `flag_reasons`: reasons to review that do not depend on confidence (for example, a written
  answer that earned 0). The confidence reason is derived, not stored.
- `grading_snapshot`: exactly what was graded (question text, reference answer, rubric, options,
  answer text, page, evaluator), so review screens show what the model was actually given.
- `grader_kind`: `deterministic`, `llm`, `slm` or `rule_blank`.

A re-grade is an upsert whose conflict clause only fires `WHERE review_status <> 'teacher_approved'`,
so no code path can overwrite a teacher's decision.

## Derived state

Two views define what the application would otherwise compute in several places:

- **`evaluation_state`**: each grade with its effective threshold (question override, else exam)
  and `needs_review`: not teacher-approved and either confidence is below the threshold or there
  are rule-based flags. Changing a threshold therefore never needs a re-grade.
- **`script_summary`**: one row per script with totals, counts and a status that is
  `processing` / `error` while the pipeline runs, then `graded` or `needs_review` once it has
  grades. `scripts.status` itself holds pipeline state only.

Both use `security_invoker`, so row-level security applies to whoever queries them.

## Integrity enforced by the database

- Marks stay within `0..max_marks`; confidence within `0..1`; a script has an `error` only when
  its status is `error`; an approved grade has a `reviewed_at`.
- One script per student per exam, and no byte-identical file twice in an exam.
- At most one `queued` or `running` job per exam (a partial unique index), so two simultaneous
  requests cannot both start work.
- Deleting an exam cascades to its scripts, extractions, grades and jobs; removing a question
  from the key removes its grades.

## Security

Row-level security is enabled on every table with no policies, and access is revoked from the
`anon` and `authenticated` roles, so the Supabase Data API exposes nothing. The backend connects
with its own database credentials. Ownership columns and policies belong with authentication,
which is not built yet.

## Changing the schema

Never edit an applied migration. Add a new file named `<UTC timestamp>_<what>.sql` and apply it
with `supabase db push` (or the SQL editor). The test suite applies every file in order to a
fresh schema, so a migration that does not apply cleanly fails CI. The files are written without
schema qualification on purpose: Supabase runs them in `public`, the tests run them in a
throwaway schema.

If you change the `evaluations` table, recreate the `evaluation_state` view in the same
migration (it selects `evaluations.*`, which Postgres freezes at creation time).

## How the application reads it

`Database` is a lazily opened connection pool (`psycopg_pool`) that returns `uuid` columns as
`str` and `numeric` as `float`. Every repository method takes an optional open connection so
several calls can share one transaction. Writes that cover a whole script (the importer, bulk
extraction) use set-based statements (`upsert_many`), because against a remote database the
number of round trips, not the amount of data, decides how long they take.
