-- 13D institutional licensing: organisation seat licences assigned by a tenant administrator.
-- Admin identity (is_admin, tenant) is verified by the API against the clinical database first;
-- these functions additionally bind every row to that tenant. Seat limits are enforced under a
-- row lock on the licence, so concurrent assignments cannot exceed the purchased seats.

CREATE UNIQUE INDEX uq_open_seat_per_practitioner ON spps_identity_dev.platform_licence_seats (practitioner_id)
 WHERE revoked_at IS NULL;

CREATE FUNCTION spps_identity_dev.admin_create_licence(p_admin integer, p_tenant integer, p_name text, p_seats integer,
 p_valid_days integer, p_quote integer)
 RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE v integer;
BEGIN
 IF p_admin<=0 OR p_tenant<=0 OR length(btrim(coalesce(p_name,''))) NOT BETWEEN 2 AND 160
    OR p_seats NOT BETWEEN 1 AND 500 OR p_valid_days NOT BETWEEN 1 AND 1100 THEN
  RAISE EXCEPTION 'invalid licence' USING ERRCODE='22023';
 END IF;
 INSERT INTO platform_organisation_licences(tenant_id,organisation_name,quote_reference,seats,status,valid_until,
   created_by_practitioner_id,created_at)
 VALUES (p_tenant,btrim(p_name),p_quote,p_seats,'ACTIVE',now()+make_interval(days=>p_valid_days),p_admin,now())
 RETURNING id INTO v;
 PERFORM audit(NULL,'licence.created','licence',v,'admin '||p_admin);
 RETURN v;
END $body$;

CREATE FUNCTION spps_identity_dev.admin_assign_seat(p_admin integer, p_tenant integer, p_licence integer, p_practitioner integer)
 RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE l platform_organisation_licences%ROWTYPE; v integer;
BEGIN
 SELECT * INTO l FROM platform_organisation_licences WHERE id=p_licence AND tenant_id=p_tenant FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'licence not found' USING ERRCODE='P0002'; END IF;
 IF l.status<>'ACTIVE' OR l.valid_until<=now() THEN RAISE EXCEPTION 'licence not active' USING ERRCODE='P0004'; END IF;
 IF (SELECT count(*) FROM platform_licence_seats WHERE licence_id=l.id AND revoked_at IS NULL) >= l.seats THEN
  RAISE EXCEPTION 'all seats are assigned' USING ERRCODE='P0004';
 END IF;
 BEGIN
  INSERT INTO platform_licence_seats(licence_id,practitioner_id,assigned_at) VALUES (l.id,p_practitioner,now()) RETURNING id INTO v;
 EXCEPTION WHEN unique_violation THEN
  RAISE EXCEPTION 'practitioner already holds a seat' USING ERRCODE='23505';
 END;
 PERFORM audit(NULL,'licence.seat_assigned','licence',l.id,'practitioner '||p_practitioner||' by admin '||p_admin);
 RETURN v;
END $body$;

CREATE FUNCTION spps_identity_dev.admin_revoke_seat(p_tenant integer, p_licence integer, p_practitioner integer)
 RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE n integer;
BEGIN
 UPDATE platform_licence_seats s SET revoked_at=now() FROM platform_organisation_licences l
  WHERE l.id=s.licence_id AND l.id=p_licence AND l.tenant_id=p_tenant AND s.practitioner_id=p_practitioner
    AND s.revoked_at IS NULL;
 GET DIAGNOSTICS n = ROW_COUNT;
 IF n=0 THEN RAISE EXCEPTION 'seat not found' USING ERRCODE='P0002'; END IF;
 PERFORM audit(NULL,'licence.seat_revoked','licence',p_licence,'practitioner '||p_practitioner);
 RETURN true;
END $body$;

CREATE FUNCTION spps_identity_dev.admin_set_licence_status(p_tenant integer, p_licence integer, p_status text)
 RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
DECLARE n integer;
BEGIN
 IF p_status NOT IN ('ACTIVE','SUSPENDED','ENDED') THEN RAISE EXCEPTION 'invalid status' USING ERRCODE='22023'; END IF;
 UPDATE platform_organisation_licences SET status=p_status WHERE id=p_licence AND tenant_id=p_tenant AND status<>'ENDED';
 GET DIAGNOSTICS n = ROW_COUNT;
 IF n=0 THEN RAISE EXCEPTION 'licence not found or ended' USING ERRCODE='P0002'; END IF;
 PERFORM audit(NULL,'licence.'||lower(p_status),'licence',p_licence,'');
 RETURN p_status;
END $body$;

CREATE FUNCTION spps_identity_dev.admin_licences(p_tenant integer)
 RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT COALESCE(jsonb_agg(jsonb_build_object('id',l.id,'organisation_name',l.organisation_name,'seats',l.seats,
   'status',l.status,'valid_until',l.valid_until,'quote_reference',l.quote_reference,
   'assigned',(SELECT COALESCE(jsonb_agg(s.practitioner_id ORDER BY s.id),'[]'::jsonb) FROM platform_licence_seats s
               WHERE s.licence_id=l.id AND s.revoked_at IS NULL)) ORDER BY l.id DESC),'[]'::jsonb)
 FROM platform_organisation_licences l WHERE l.tenant_id=p_tenant
$body$;

-- Membership entitlement via an active institutional seat.
CREATE FUNCTION spps_identity_dev.practitioner_has_seat(p_practitioner integer, p_tenant integer)
 RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,spps_identity_dev AS $body$
 SELECT EXISTS (SELECT 1 FROM platform_licence_seats s JOIN platform_organisation_licences l ON l.id=s.licence_id
  WHERE s.practitioner_id=p_practitioner AND s.revoked_at IS NULL AND l.tenant_id=p_tenant
    AND l.status='ACTIVE' AND l.valid_until>now())
$body$;

DO $$ DECLARE f record; BEGIN
 FOR f IN SELECT p.oid::regprocedure AS sig FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
          WHERE n.nspname='spps_identity_dev' AND p.proname IN ('admin_create_licence','admin_assign_seat',
           'admin_revoke_seat','admin_set_licence_status','admin_licences','practitioner_has_seat') LOOP
  EXECUTE format('ALTER FUNCTION %s OWNER TO spps_identity_owner', f.sig);
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', f.sig);
  EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO spps_identity_auth', f.sig);
 END LOOP;
END $$;
