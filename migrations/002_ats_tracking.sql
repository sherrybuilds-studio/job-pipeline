-- 002: application tracking after the letter is written.
-- stage values: applied | replied | interview | offer | rejected | withdrawn
-- response_notes carries the "sent YYYY-MM-DD" stamp that the follow-up
-- schedule and the summary treat as the only evidence of a send.

alter table applications add column if not exists cv_path          text;
alter table applications add column if not exists cv_keywords      text[];
alter table applications add column if not exists job_folder       text;
alter table applications add column if not exists stage            text;
alter table applications add column if not exists response_at      timestamptz;
alter table applications add column if not exists response_type    text;
alter table applications add column if not exists response_notes   text;
alter table applications add column if not exists followup_count   int default 0;
alter table applications add column if not exists last_followup_at timestamptz;

create index if not exists applications_stage_idx on applications (stage);
create index if not exists applications_followup_idx on applications (followup_count);
