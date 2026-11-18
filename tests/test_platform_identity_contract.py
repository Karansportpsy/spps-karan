"""Stage 13A design-only checks. Not API, PostgreSQL RLS or hosted acceptance."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import postgresql

from server import models as legacy
from server.platform_identity_models import IdentityBase
from server.platform_identity_policy import (
    CaseLink, GuardianGrant, PortalPrincipal, SHAREABLE_PROJECTIONS,
    may_view_released_projection,
)

NOW = datetime(2026, 11, 11, tzinfo=timezone.utc)


def adult():
    return PortalPrincipal(
        account_id=10, person_id=100, verified=True,
        session_active=True, roles=frozenset({"ATHLETE"}),
    )


def guardian():
    return PortalPrincipal(
        account_id=20, person_id=200, verified=True,
        session_active=True, roles=frozenset({"GUARDIAN"}),
    )


def active_link(practitioner_id=1):
    return CaseLink(
        person_id=100, practitioner_id=practitioner_id,
        case_owner_practitioner_id=practitioner_id,
        status="ACTIVE", practitioner_approved=True,
        consent_current=True,
        released_projections=frozenset({"assigned_tasks", "appointment_metadata"}),
    )


def view(principal=None, *, resource="assigned_tasks", link=None,
         subject=100, practitioner_id=1, grant=None):
    return may_view_released_projection(
        principal if principal is not None else adult(),
        subject_person_id=subject, resource_practitioner_id=practitioner_id,
        resource=resource, link=active_link() if link is None else link,
        guardian=grant, at=NOW,
    )


def test_new_metadata_is_independent_and_legacy_keeps_exactly_33_tables():
    assert len(legacy.Base.metadata.tables) == 33
    new = IdentityBase.metadata.tables
    assert len(new) == 22  # 13A 11 + 13B 3 + 13C 5 + video + licences, seats
    assert all(name.startswith("spps_identity_dev.") for name in new)
    assert not set(new).intersection(legacy.Base.metadata.tables)
    assert "spps_identity_dev.platform_accounts" in new
    assert "spps_identity_dev.guardian_authorities" in new
    assert "spps_identity_dev.practitioner_athlete_links" in new


def test_draft_schema_compiles_as_postgresql_without_modifying_any_database():
    for table in IdentityBase.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        assert "CREATE TABLE spps_identity_dev." in ddl
    accounts = IdentityBase.metadata.tables["spps_identity_dev.platform_accounts"]
    assert "password_hash" in accounts.c
    sessions = IdentityBase.metadata.tables["spps_identity_dev.platform_account_sessions"]
    assert "token_hash" in sessions.c
    assert "csrf_hash" in sessions.c
    assert "token" not in sessions.c
    links = IdentityBase.metadata.tables["spps_identity_dev.practitioner_athlete_links"]
    assert "practitioner_id" in links.c
    assert "athlete_record_id" in links.c


def test_adult_with_verified_link_can_view_explicitly_released_task():
    assert view()


def test_private_resources_are_always_denied_even_to_linked_athlete():
    for resource in (
        "case_notes", "draft_reports", "all_assessments",
        "private_injury_history", "payment_secrets", "unknown",
    ):
        assert not view(resource=resource)


def test_unverified_or_logged_out_or_unrelated_person_is_denied():
    assert not view(replace(adult(), verified=False))
    assert not view(replace(adult(), session_active=False))
    assert not view(replace(adult(), person_id=999))
    assert not view(replace(adult(), roles=frozenset({"ADMIN"})))


def test_unapproved_expired_revoked_or_unconsented_link_is_denied():
    assert not view(link=replace(active_link(), status="REQUESTED"))
    assert not view(link=replace(active_link(), practitioner_approved=False))
    assert not view(link=replace(active_link(), consent_current=False))
    assert not view(link=replace(active_link(), revoked_at=NOW))
    assert not view(link=replace(active_link(), expires_at=NOW - timedelta(seconds=1)))


def test_nested_identity_and_case_owner_substitutions_are_denied():
    assert not view(subject=100, practitioner_id=2)
    assert not view(link=replace(active_link(), person_id=999))
    assert not view(link=replace(active_link(), case_owner_practitioner_id=2))
    assert not view(link=replace(active_link(), released_projections=frozenset()))
    assert view(link=active_link(practitioner_id=2), practitioner_id=2)
    assert not view(link=active_link(practitioner_id=2), practitioner_id=1)


def test_guardian_requires_verified_authority_subject_and_specific_scope():
    dependent_link = replace(active_link(), person_id=300)
    grant = GuardianGrant(
        guardian_account_id=20, dependent_person_id=300, status="VERIFIED",
        scopes=frozenset({"assigned_tasks"}),
    )
    assert view(guardian(), subject=300, link=dependent_link, grant=grant)
    assert not view(guardian(), subject=300, link=dependent_link,
                    resource="appointment_metadata", grant=grant)
    assert not view(guardian(), subject=300, link=dependent_link,
                    grant=replace(grant, status="PENDING_VERIFICATION"))
    assert not view(guardian(), subject=300, link=dependent_link,
                    grant=replace(grant, guardian_account_id=999))
    assert not view(guardian(), subject=300, link=dependent_link,
                    grant=replace(grant, dependent_person_id=999))
    assert not view(guardian(), subject=300, link=dependent_link,
                    grant=replace(grant, revoked_at=NOW))
    assert not view(guardian(), subject=300, link=dependent_link,
                    grant=replace(grant, expires_at=NOW - timedelta(seconds=1)))
    assert not view(guardian(), subject=300, link=dependent_link, grant=None)


def test_guardian_only_role_cannot_impersonate_athlete_and_admin_no_bypass():
    assert not view(guardian())
    admin = replace(adult(), roles=frozenset({"ADMIN"}))
    assert not view(admin)
    assert "case_notes" not in SHAREABLE_PROJECTIONS


def test_naive_datetime_fails_closed():
    assert not may_view_released_projection(
        adult(), subject_person_id=100, resource_practitioner_id=1,
        resource="assigned_tasks", link=active_link(),
        at=datetime(2026, 11, 11),
    )
