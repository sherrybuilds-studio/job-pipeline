-- 003: who to address a follow-up to. Filled in by hand when the posting or a
-- reply names a contact; the follow-up draft falls back to "hiring team".

alter table applications add column if not exists contact_name  text;
alter table applications add column if not exists contact_email text;
