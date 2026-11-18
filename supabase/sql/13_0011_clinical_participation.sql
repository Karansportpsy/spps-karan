-- SPPS production database, step 13 of the serial set: 0011_clinical_participation
-- Run in order in the Supabase SQL editor as the default postgres role. Each file is one transaction.
-- Source: migrations/0011_clinical_participation.sql
BEGIN;
SET LOCAL ROLE spps_identity_owner;
-- Stage 13C: athlete clinical participation (platform/identity database only).
-- Practitioner drafts -> assigns -> athlete accepts/declines -> athlete (or guardian proxy)
-- submits source-tagged self-reports -> practitioner reviews. Practitioners may also
-- RELEASE explicit summaries. Private notes, draft reports and assessment narratives in
-- the clinical database are never copied here or exposed to the portal.
-- Practitioner functions take a server-verified practitioner_id (clinical athlete_for
-- runs first in the API); portal functions derive the actor from the session token.

-- Submissions and reviews are append-only clinical records.
DROP POLICY identity_owner_update ON spps_identity_dev.platform_task_submissions;
DROP POLICY identity_owner_update ON spps_identity_dev.platform_task_reviews;
CREATE TRIGGER submissions_append_only BEFORE UPDATE OR DELETE ON spps_identity_dev.platform_task_submissions
 FOR EACH ROW EXECUTE FUNCTION spps_identity_dev.consent_events_append_only();
CREATE TRIGGER reviews_append_only BEFORE UPDATE OR DELETE ON spps_identity_dev.platform_task_reviews
 FOR EACH ROW EXECUTE FUNCTION spps_identity_dev.consent_events_append_only();

-- Programme visible to an actor? scope assigned_tasks (view) or proxy_submission (act) for guardians.
CREATE FUNCTION spps_identity_dev.programme_actor(p_actor integer, p_person integer, p_scope text)
 RETURNS TABLE(allowed boolean, authority_id integer)
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT x.allowed, x.authority_id FROM acting_authority(p_actor, p_person, p_scope) x
$body$;

-- ---------------------------------------------------------------- practitioner side
CREATE FUNCTION spps_identity_dev.practitioner_programme(p_practitioner_id integer, p_programme_id integer)
 RETURNS spps_identity_dev.platform_programmes LANGUAGE plpgsql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE p platform_programmes%ROWTYPE;
BEGIN
 SELECT * INTO p FROM platform_programmes WHERE id=p_programme_id AND practitioner_id=p_practitioner_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'programme not found' USING ERRCODE='P0002'; END IF;
 RETURN p;
END $body$;

CREATE FUNCTION spps_identity_dev.practitioner_create_programme(
 p_practitioner_id integer, p_athlete_record_id integer, p_title text, p_goal text)
 RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE l record; v_id integer;
BEGIN
 SELECT * INTO l FROM practitioner_active_link(p_practitioner_id, p_athlete_record_id);
 IF l.link_id IS NULL THEN RAISE EXCEPTION 'no active portal link for this athlete' USING ERRCODE='P0004'; END IF;
 IF length(btrim(coalesce(p_title,''))) NOT BETWEEN 3 AND 140 OR length(coalesce(p_goal,''))>1000 THEN
  RAISE EXCEPTION 'invalid programme' USING ERRCODE='22023';
 END IF;
 INSERT INTO platform_programmes(practitioner_id,athlete_record_id,link_id,person_id,title,goal,status,created_at)
 VALUES (p_practitioner_id,p_athlete_record_id,l.link_id,l.person_id,btrim(p_title),btrim(coalesce(p_goal,'')),'DRAFT',now())
 RETURNING id INTO v_id;
 PERFORM audit(NULL,'programme.created','programme',v_id,'practitioner '||p_practitioner_id);
 RETURN v_id;
END $body$;

CREATE FUNCTION spps_identity_dev.practitioner_add_task(
 p_practitioner_id integer, p_programme_id integer, p_title text, p_instructions text, p_frequency text, p_due timestamptz)
 RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE p platform_programmes%ROWTYPE := practitioner_programme(p_practitioner_id, p_programme_id); v_id integer;
BEGIN
 IF p.status IN ('DECLINED','COMPLETED','DISCONTINUED') THEN RAISE EXCEPTION 'programme closed' USING ERRCODE='P0004'; END IF;
 IF length(btrim(coalesce(p_title,''))) NOT BETWEEN 3 AND 140 OR length(coalesce(p_instructions,''))>2000
    OR p_frequency NOT IN ('once','daily','weekly','as_needed') THEN
  RAISE EXCEPTION 'invalid task' USING ERRCODE='22023';
 END IF;
 INSERT INTO platform_programme_tasks(programme_id,title,instructions,frequency,due_date,status,revision,updated_at)
 VALUES (p.id,btrim(p_title),btrim(coalesce(p_instructions,'')),p_frequency,p_due,'ACTIVE',1,now()) RETURNING id INTO v_id;
 IF p.status IN ('ASSIGNED','ACCEPTED') THEN
  PERFORM notify_account((SELECT requested_by_account_id FROM practitioner_athlete_links WHERE id=p.link_id),
                         'programme.task_added','programme',p.id);
 END IF;
 RETURN v_id;
END $body$;

-- Optimistic concurrency: the caller states the revision it edited.
CREATE FUNCTION spps_identity_dev.practitioner_update_task(
 p_practitioner_id integer, p_task_id integer, p_title text, p_instructions text, p_frequency text,
 p_due timestamptz, p_expected_revision integer)
 RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE t platform_programme_tasks%ROWTYPE;
BEGIN
 SELECT k.* INTO t FROM platform_programme_tasks k JOIN platform_programmes p ON p.id=k.programme_id
  WHERE k.id=p_task_id AND p.practitioner_id=p_practitioner_id FOR UPDATE OF k;
 IF NOT FOUND THEN RAISE EXCEPTION 'task not found' USING ERRCODE='P0002'; END IF;
 IF t.status<>'ACTIVE' THEN RAISE EXCEPTION 'task discontinued' USING ERRCODE='P0004'; END IF;
 IF t.revision<>p_expected_revision THEN RAISE EXCEPTION 'task changed since you loaded it' USING ERRCODE='P0006'; END IF;
 IF length(btrim(coalesce(p_title,''))) NOT BETWEEN 3 AND 140 OR length(coalesce(p_instructions,''))>2000
    OR p_frequency NOT IN ('once','daily','weekly','as_needed') THEN
  RAISE EXCEPTION 'invalid task' USING ERRCODE='22023';
 END IF;
 UPDATE platform_programme_tasks SET title=btrim(p_title), instructions=btrim(coalesce(p_instructions,'')),
   frequency=p_frequency, due_date=p_due, revision=revision+1, updated_at=now() WHERE id=t.id;
 RETURN t.revision+1;
END $body$;

CREATE FUNCTION spps_identity_dev.practitioner_discontinue_task(p_practitioner_id integer, p_task_id integer)
 RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE n integer;
BEGIN
 UPDATE platform_programme_tasks k SET status='DISCONTINUED', revision=revision+1, updated_at=now()
  FROM platform_programmes p WHERE p.id=k.programme_id AND k.id=p_task_id AND p.practitioner_id=p_practitioner_id
   AND k.status='ACTIVE';
 GET DIAGNOSTICS n = ROW_COUNT;
 IF n=0 THEN RAISE EXCEPTION 'task not found' USING ERRCODE='P0002'; END IF;
 RETURN true;
END $body$;

CREATE FUNCTION spps_identity_dev.practitioner_programme_transition(p_practitioner_id integer, p_programme_id integer, p_to text)
 RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE p platform_programmes%ROWTYPE := practitioner_programme(p_practitioner_id, p_programme_id); v_ok boolean;
BEGIN
 v_ok := CASE p_to
   WHEN 'ASSIGNED' THEN p.status='DRAFT'
   WHEN 'PAUSED' THEN p.status='ACCEPTED'
   WHEN 'ACCEPTED' THEN p.status='PAUSED'
   WHEN 'COMPLETED' THEN p.status IN ('ACCEPTED','PAUSED')
   WHEN 'DISCONTINUED' THEN p.status IN ('DRAFT','ASSIGNED','ACCEPTED','PAUSED')
   ELSE false END;
 IF NOT v_ok THEN RAISE EXCEPTION 'programme cannot move from % to %', p.status, p_to USING ERRCODE='P0004'; END IF;
 IF p_to='ASSIGNED' THEN
  IF NOT link_is_active(p.link_id) THEN RAISE EXCEPTION 'portal link is not active' USING ERRCODE='P0004'; END IF;
  IF NOT EXISTS (SELECT 1 FROM platform_programme_tasks WHERE programme_id=p.id AND status='ACTIVE') THEN
   RAISE EXCEPTION 'add at least one task first' USING ERRCODE='P0004';
  END IF;
 END IF;
 UPDATE platform_programmes SET status=p_to, assigned_at=CASE WHEN p_to='ASSIGNED' THEN now() ELSE assigned_at END
  WHERE id=p.id;
 PERFORM notify_account((SELECT requested_by_account_id FROM practitioner_athlete_links WHERE id=p.link_id),
                        'programme.'||lower(p_to),'programme',p.id);
 PERFORM audit(NULL,'programme.'||lower(p_to),'programme',p.id,'practitioner '||p_practitioner_id);
 RETURN p_to;
END $body$;

CREATE FUNCTION spps_identity_dev.practitioner_review_submission(p_practitioner_id integer, p_submission_id integer, p_feedback text)
 RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v_prog platform_programmes%ROWTYPE; v_id integer;
BEGIN
 SELECT p.* INTO v_prog FROM platform_task_submissions s JOIN platform_programme_tasks k ON k.id=s.task_id
  JOIN platform_programmes p ON p.id=k.programme_id WHERE s.id=p_submission_id AND p.practitioner_id=p_practitioner_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'submission not found' USING ERRCODE='P0002'; END IF;
 IF length(btrim(coalesce(p_feedback,''))) NOT BETWEEN 2 AND 1000 THEN RAISE EXCEPTION 'invalid feedback' USING ERRCODE='22023'; END IF;
 INSERT INTO platform_task_reviews(submission_id,practitioner_id,feedback,created_at)
 VALUES (p_submission_id,p_practitioner_id,btrim(p_feedback),now()) RETURNING id INTO v_id;
 PERFORM notify_account((SELECT requested_by_account_id FROM practitioner_athlete_links WHERE id=v_prog.link_id),
                        'programme.feedback','programme',v_prog.id);
 RETURN v_id;
END $body$;

CREATE FUNCTION spps_identity_dev.practitioner_release_summary(
 p_practitioner_id integer, p_athlete_record_id integer, p_kind text, p_title text, p_body text)
 RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE l record; v_id integer;
BEGIN
 SELECT * INTO l FROM practitioner_active_link(p_practitioner_id, p_athlete_record_id);
 IF l.link_id IS NULL THEN RAISE EXCEPTION 'no active portal link for this athlete' USING ERRCODE='P0004'; END IF;
 IF p_kind NOT IN ('assessment_summary','progress_summary','session_summary')
    OR length(btrim(coalesce(p_title,''))) NOT BETWEEN 3 AND 140 OR length(btrim(coalesce(p_body,''))) NOT BETWEEN 5 AND 2000 THEN
  RAISE EXCEPTION 'invalid summary' USING ERRCODE='22023';
 END IF;
 INSERT INTO platform_released_summaries(practitioner_id,athlete_record_id,link_id,person_id,kind,title,body,released_at)
 VALUES (p_practitioner_id,p_athlete_record_id,l.link_id,l.person_id,p_kind,btrim(p_title),btrim(p_body),now())
 RETURNING id INTO v_id;
 PERFORM notify_account((SELECT requested_by_account_id FROM practitioner_athlete_links WHERE id=l.link_id),
                        'summary.released','summary',v_id);
 PERFORM audit(NULL,'summary.released','summary',v_id,p_kind);
 RETURN v_id;
END $body$;

CREATE FUNCTION spps_identity_dev.practitioner_withdraw_summary(p_practitioner_id integer, p_summary_id integer)
 RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE n integer;
BEGIN
 UPDATE platform_released_summaries SET withdrawn_at=now()
  WHERE id=p_summary_id AND practitioner_id=p_practitioner_id AND withdrawn_at IS NULL;
 GET DIAGNOSTICS n = ROW_COUNT;
 IF n=0 THEN RAISE EXCEPTION 'summary not found' USING ERRCODE='P0002'; END IF;
 PERFORM audit(NULL,'summary.withdrawn','summary',p_summary_id,'');
 RETURN true;
END $body$;

-- Full practitioner view for one athlete record (programmes, tasks, submissions, reviews, summaries).
CREATE FUNCTION spps_identity_dev.practitioner_participation(p_practitioner_id integer, p_athlete_record_id integer)
 RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT jsonb_build_object(
  'active_link', (SELECT to_jsonb(x) FROM practitioner_active_link(p_practitioner_id, p_athlete_record_id) x),
  'programmes', COALESCE((SELECT jsonb_agg(jsonb_build_object(
     'id',p.id,'title',p.title,'goal',p.goal,'status',p.status,'response_reason',p.response_reason,
     'assigned_at',p.assigned_at,'responded_at',p.responded_at,
     'tasks', COALESCE((SELECT jsonb_agg(jsonb_build_object(
        'id',k.id,'title',k.title,'instructions',k.instructions,'frequency',k.frequency,'due_date',k.due_date,
        'status',k.status,'revision',k.revision,
        'submissions', COALESCE((SELECT jsonb_agg(jsonb_build_object(
           'id',s.id,'completed',s.completed,'self_rating',s.self_rating,'reflection',s.reflection,
           'source',s.source,'task_revision',s.task_revision,'submitted_at',s.submitted_at,
           'reviews', COALESCE((SELECT jsonb_agg(jsonb_build_object('feedback',r.feedback,'created_at',r.created_at)
                      ORDER BY r.id) FROM platform_task_reviews r WHERE r.submission_id=s.id),'[]'::jsonb))
           ORDER BY s.id DESC) FROM platform_task_submissions s WHERE s.task_id=k.id),'[]'::jsonb))
        ORDER BY k.id) FROM platform_programme_tasks k WHERE k.programme_id=p.id),'[]'::jsonb))
     ORDER BY p.id DESC) FROM platform_programmes p
     WHERE p.practitioner_id=p_practitioner_id AND p.athlete_record_id=p_athlete_record_id),'[]'::jsonb),
  'summaries', COALESCE((SELECT jsonb_agg(jsonb_build_object('id',m.id,'kind',m.kind,'title',m.title,'body',m.body,
      'released_at',m.released_at,'withdrawn_at',m.withdrawn_at) ORDER BY m.id DESC)
     FROM platform_released_summaries m WHERE m.practitioner_id=p_practitioner_id
      AND m.athlete_record_id=p_athlete_record_id),'[]'::jsonb))
$body$;

-- ---------------------------------------------------------------- portal side
CREATE FUNCTION spps_identity_dev.portal_participation(p_token_hash text)
 RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v_actor integer := require_actor(p_token_hash,NULL);
BEGIN
 RETURN jsonb_build_object(
  'programmes', COALESCE((SELECT jsonb_agg(jsonb_build_object(
     'id',p.id,'person_id',p.person_id,'practitioner_id',p.practitioner_id,'title',p.title,'goal',p.goal,
     'status',p.status,'assigned_at',p.assigned_at,
     'can_act', EXISTS (SELECT 1 FROM programme_actor(v_actor,p.person_id,'proxy_submission') a WHERE a.allowed),
     'can_see_submissions', EXISTS (SELECT 1 FROM programme_actor(v_actor,p.person_id,'own_submissions') a WHERE a.allowed),
     'tasks', COALESCE((SELECT jsonb_agg(jsonb_build_object(
        'id',k.id,'title',k.title,'instructions',k.instructions,'frequency',k.frequency,'due_date',k.due_date,
        'revision',k.revision,
        'submission_count',(SELECT count(*) FROM platform_task_submissions s WHERE s.task_id=k.id),
        'submissions', CASE WHEN EXISTS (SELECT 1 FROM programme_actor(v_actor,p.person_id,'own_submissions') a WHERE a.allowed)
          THEN COALESCE((SELECT jsonb_agg(jsonb_build_object('id',s.id,'completed',s.completed,'self_rating',s.self_rating,
               'reflection',s.reflection,'source',s.source,'submitted_at',s.submitted_at,
               'feedback',(SELECT jsonb_agg(r.feedback ORDER BY r.id) FROM platform_task_reviews r WHERE r.submission_id=s.id))
               ORDER BY s.id DESC) FROM platform_task_submissions s WHERE s.task_id=k.id),'[]'::jsonb)
          ELSE '[]'::jsonb END)
        ORDER BY k.id) FROM platform_programme_tasks k WHERE k.programme_id=p.id AND k.status='ACTIVE'),'[]'::jsonb))
     ORDER BY p.id DESC)
     FROM platform_programmes p
     WHERE p.status NOT IN ('DRAFT') AND link_is_active(p.link_id)
       AND EXISTS (SELECT 1 FROM programme_actor(v_actor,p.person_id,'assigned_tasks') a WHERE a.allowed)),'[]'::jsonb),
  'summaries', COALESCE((SELECT jsonb_agg(jsonb_build_object('id',m.id,'person_id',m.person_id,
      'practitioner_id',m.practitioner_id,'kind',m.kind,'title',m.title,'body',m.body,'released_at',m.released_at)
      ORDER BY m.id DESC)
     FROM platform_released_summaries m
     WHERE m.withdrawn_at IS NULL AND link_is_active(m.link_id)
       AND EXISTS (SELECT 1 FROM programme_actor(v_actor,m.person_id,'released_assessment_summary') a WHERE a.allowed)),'[]'::jsonb));
END $body$;

CREATE FUNCTION spps_identity_dev.portal_respond_programme(p_token_hash text, p_programme_id integer, p_accept boolean, p_reason text)
 RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v_actor integer := require_actor(p_token_hash,NULL); p platform_programmes%ROWTYPE; v_to text;
BEGIN
 SELECT * INTO p FROM platform_programmes WHERE id=p_programme_id FOR UPDATE;
 IF NOT FOUND OR NOT link_is_active(p.link_id)
    OR NOT EXISTS (SELECT 1 FROM programme_actor(v_actor,p.person_id,'proxy_submission') a WHERE a.allowed) THEN
  RAISE EXCEPTION 'programme not found' USING ERRCODE='P0002';
 END IF;
 IF p.status<>'ASSIGNED' THEN RAISE EXCEPTION 'programme is %', p.status USING ERRCODE='P0004'; END IF;
 IF p_accept AND NOT consent_is_current(p.person_id, p.practitioner_id, 'PROGRAMME_PARTICIPATION') THEN
  RAISE EXCEPTION 'programme consent required' USING ERRCODE='P0003';
 END IF;
 v_to := CASE WHEN p_accept THEN 'ACCEPTED' ELSE 'DECLINED' END;
 UPDATE platform_programmes SET status=v_to, responded_at=now(),
   response_reason=nullif(left(btrim(coalesce(p_reason,'')),240),'') WHERE id=p.id;
 PERFORM notify_practitioner(p.practitioner_id,'programme.'||lower(v_to),'programme',p.id);
 PERFORM audit(v_actor,'programme.'||lower(v_to),'programme',p.id,'');
 RETURN v_to;
END $body$;

CREATE FUNCTION spps_identity_dev.portal_submit_task(
 p_token_hash text, p_task_id integer, p_completed boolean, p_rating integer, p_reflection text)
 RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v_actor integer := require_actor(p_token_hash,NULL); k platform_programme_tasks%ROWTYPE;
        p platform_programmes%ROWTYPE; v_allowed boolean; v_authority integer; v_id integer;
BEGIN
 SELECT * INTO k FROM platform_programme_tasks WHERE id=p_task_id;
 IF FOUND THEN SELECT * INTO p FROM platform_programmes WHERE id=k.programme_id; END IF;
 IF k.id IS NULL THEN RAISE EXCEPTION 'task not found' USING ERRCODE='P0002'; END IF;
 SELECT a.allowed, a.authority_id INTO v_allowed, v_authority FROM programme_actor(v_actor,p.person_id,'proxy_submission') a;
 -- Revoked/withdrawn links, unrelated accounts and missing scope all look like "not found".
 IF v_allowed IS NOT TRUE OR NOT link_is_active(p.link_id) THEN
  RAISE EXCEPTION 'task not found' USING ERRCODE='P0002';
 END IF;
 IF p.status<>'ACCEPTED' OR k.status<>'ACTIVE' THEN RAISE EXCEPTION 'task is not open' USING ERRCODE='P0004'; END IF;
 IF NOT consent_is_current(p.person_id, p.practitioner_id, 'PROGRAMME_PARTICIPATION') THEN
  RAISE EXCEPTION 'programme consent required' USING ERRCODE='P0003';
 END IF;
 IF (p_rating IS NOT NULL AND p_rating NOT BETWEEN 0 AND 10) OR length(coalesce(p_reflection,''))>1000 THEN
  RAISE EXCEPTION 'invalid submission' USING ERRCODE='22023';
 END IF;
 IF (SELECT count(*) FROM platform_task_submissions WHERE task_id=k.id AND submitted_at>now()-interval '1 day') >= 20 THEN
  RAISE EXCEPTION 'daily submission limit reached' USING ERRCODE='22023';
 END IF;
 INSERT INTO platform_task_submissions(task_id,task_revision,person_id,submitted_by_account_id,source,completed,
   self_rating,reflection,submitted_at)
 VALUES (k.id,k.revision,p.person_id,v_actor,CASE WHEN v_authority IS NULL THEN 'ATHLETE_SELF_REPORT' ELSE 'GUARDIAN_PROXY' END,
   coalesce(p_completed,false),p_rating,btrim(coalesce(p_reflection,'')),now())
 RETURNING id INTO v_id;
 PERFORM notify_practitioner(p.practitioner_id,'programme.submission','programme',p.id);
 RETURN v_id;
END $body$;

DO $$ DECLARE f record; BEGIN
 FOR f IN SELECT p.oid::regprocedure AS sig, p.proname FROM pg_proc p
          JOIN pg_namespace n ON n.oid=p.pronamespace
          WHERE n.nspname='spps_identity_dev' AND p.proname IN (
           'programme_actor','practitioner_programme','practitioner_create_programme','practitioner_add_task',
           'practitioner_update_task','practitioner_discontinue_task','practitioner_programme_transition',
           'practitioner_review_submission','practitioner_release_summary','practitioner_withdraw_summary',
           'practitioner_participation','portal_participation','portal_respond_programme','portal_submit_task') LOOP
  EXECUTE format('ALTER FUNCTION %s OWNER TO spps_identity_owner', f.sig);
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', f.sig);
  IF f.proname NOT IN ('programme_actor','practitioner_programme') THEN
   EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO spps_identity_auth', f.sig);
  END IF;
 END LOOP;
END $$;
RESET ROLE;
INSERT INTO spps_ops.migration_history(name,sha256) VALUES ('0011_clinical_participation.sql','33e77be46aaa3fe294c1e05973fb76d2551633bc77a2ae58869fd34f4a9ca0bf') ON CONFLICT (name) DO NOTHING;
COMMIT;
