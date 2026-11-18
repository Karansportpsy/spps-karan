"""Explicit milestone surface. Other workflows retain their existing local profile."""
import re
import os

READS = re.compile(r'^/(?:health/(?:live|ready)|api/runtime|api/auth/me|api/(?:dashboard|enquiries|athletes|factors|interventions|account/tier|bookings|slots|practitioner/(?:profile|programmes|programme-enquiries))|api/athletes/\d+(?:/(?:observations|reports|assessments/offline|indices|injury))?|api/cases/\d+)$')
WRITES = re.compile(r'^/api/(?:auth/(?:login|logout)|athletes|cases|factors|observations|assessments/offline|athletes/\d+/service-consent)$')

# Scoped local clinical writes: still prohibit payment, marketplace and room-token actions.
CLINICAL_WRITES = re.compile(r'^/api/(?:notes(?:/\d+/finalize)?|assignments|reports|injury/episodes(?:/\d+/checkins)?)$')


# Stage 13A.5 athlete/guardian portal (only when SPPS_PORTAL_ENABLED=true).
PORTAL_READS = re.compile(r'^/api/portal/(?:auth/me|me|dependants|review/authorities|consent-policy|consents/\d+|links'
                          r'|links/\d+/slots|bookings|notifications|programmes)$')
# Platform-booking video: ticket-authenticated room API (re-validated per request).
VIDEO_READS = re.compile(r'^/api/video/\d+/(?:status|ice)$')
VIDEO_WRITES = re.compile(r'^/api/video/\d+/consent$')
PORTAL_WRITES = re.compile(r'^/api/portal/(?:auth/(?:register|resend-verification|verify-email|login|logout|logout-all|password-reset/(?:request|confirm)|password/change)'
                           r'|dependants|guardian-authorities(?:/\d+/(?:evidence|revoke))?|review/authorities/\d+/(?:decision|revoke)|consents'
                           r'|links/(?:redeem|preview)|links/\d+/revoke'
                           r'|bookings|bookings/\d+/(?:pay|verify-payment|cancel|reschedule-response)'
                           r'|programmes/\d+/respond|tasks/\d+/submit|bookings/\d+/video-ticket)$')
# 13A.7 practitioner-side link management (also requires the portal flag).
LINK_READS = re.compile(r'^/api/(?:portal-links|athletes/\d+/portal-links|booking-requests|practitioner/notifications'
                        r'|athletes/\d+/participation)$')
LINK_WRITES = re.compile(r'^/api/(?:athletes/\d+/portal-invitations|portal-links/\d+/decision|slots'
                         r'|booking-requests/\d+/(?:decision|reschedule|cancel|complete)|payments/webhook/razorpay'
                         r'|athletes/\d+/(?:programmes|released-summaries)|programmes/\d+/(?:tasks|status)'
                         r'|programme-tasks/\d+/(?:update|discontinue)|task-submissions/\d+/review|released-summaries/\d+/withdraw'
                         r'|booking-requests/\d+/(?:video-ticket|end-video))$')


ADMIN_READS = re.compile(r'^/api/admin/(?:practitioner-reviews|licences)$')
ADMIN_WRITES = re.compile(r'^/api/admin/(?:practitioners/\d+/(?:credentials|publication)'
                          r'|practitioners/\d+/approval|licences|licences/\d+/(?:seats|seats/\d+/revoke|status))$')



# Public free Beta API routes are allowlisted; database authorisation remains
# separately enforced by each restricted PostgreSQL role and its RLS policies.
FREE_PUBLIC_READS = re.compile(r'^/api/public/(?:services|practitioner-plans|supabase-auth-config|programmes|practitioners(?:/\d+(?:/(?:photo|slots))?)?)$')
FREE_PUBLIC_WRITES = re.compile(r'^/api/public/(?:enquiries|practitioner-registration|practitioner-application/(?:status|update))$')

# Account lifecycle (phase A). Reachable only while SPPS_ACCOUNT_LIFECYCLE_ENABLED=true.
LIFECYCLE_READS = re.compile(r'^/api/account/lifecycle$')
LIFECYCLE_WRITES = re.compile(r'^/api/account/(?:deactivate|reactivate|export)$')


def lifecycle_enabled():
    return os.getenv('SPPS_ACCOUNT_LIFECYCLE_ENABLED', 'false').lower() == 'true'


def hosted_free_public():
    return (os.getenv('SPPS_HOSTED_RLS', 'false').lower() == 'true'
            and os.getenv('SPPS_BETA_V1_FREE', 'false').lower() == 'true')


def portal_enabled():
    return os.getenv('SPPS_PORTAL_ENABLED', 'false').lower() == 'true'


def allowed(method, path):
    # v2 routes carry their own allowlist (server/v2/allowlist.py); flag-off v2 surfaces are refused.
    from .v2.allowlist import v2_allowed
    decided = v2_allowed(method, path)
    if decided is not None:
        return decided
    if hosted_free_public():
        if method in ('GET', 'HEAD') and FREE_PUBLIC_READS.fullmatch(path):
            return True
        if method == 'POST' and FREE_PUBLIC_WRITES.fullmatch(path):
            return True
    if method in ('GET', 'HEAD') and ADMIN_READS.fullmatch(path):
        return True
    if method == 'POST' and ADMIN_WRITES.fullmatch(path):
        return True
    if path=='/api/auth/supabase-exchange' and method=='POST':
        return True
    if lifecycle_enabled():
        if method in ('GET', 'HEAD') and LIFECYCLE_READS.fullmatch(path):
            return True
        if method == 'POST' and LIFECYCLE_WRITES.fullmatch(path):
            return True
    if path=='/api/practitioner/profile' and method=='PUT':
        return True  # restricted self-edit through the SECURITY DEFINER function (0023/0029)
    if path.startswith('/api/portal/'):
        if not portal_enabled():
            return False
        return bool(PORTAL_READS.fullmatch(path)) if method in ('GET', 'HEAD') else (
            method == 'POST' and bool(PORTAL_WRITES.fullmatch(path)))
    if portal_enabled():
        if method in ('GET', 'HEAD') and VIDEO_READS.fullmatch(path):
            return True
        if method == 'POST' and VIDEO_WRITES.fullmatch(path):
            return True
        if method in ('GET', 'HEAD') and LINK_READS.fullmatch(path):
            return True
        if method == 'POST' and LINK_WRITES.fullmatch(path):
            return True
    if method in ('GET', 'HEAD'):
        return bool(READS.fullmatch(path)) or not path.startswith('/api/')
    return method == 'POST' and (
        bool(WRITES.fullmatch(path))
        or ((os.getenv('SPPS_DEV_CLINICAL_PARITY', 'false').lower() == 'true'
             # Hosted RLS startup refuses to run unless the clinical write policies are installed.
             or os.getenv('SPPS_HOSTED_RLS', 'false').lower() == 'true')
            and bool(CLINICAL_WRITES.fullmatch(path)))
    )
