"""Transaction-local identity for the opt-in, isolated PostgreSQL milestone.

Only authenticated server code sets these values. Database credentials and SQL
execution are never exposed to clients; GUCs are not an SQL-injection boundary.
"""
from sqlalchemy import event, text
from sqlalchemy.engine import make_url


def require_local_database(url):
    parsed = make_url(url)
    if (parsed.get_backend_name() != 'postgresql'
            or parsed.host not in ('localhost', '127.0.0.1', '::1')
            or not (parsed.database or '').startswith('spps_dev')
            or parsed.query):
        raise RuntimeError('Integration development requires a loopback spps_dev PostgreSQL database without URL options')
    return parsed


def apply_identity(connection, identity):
    connection.execute(text("SELECT set_config('spps.practitioner_id', :pid, true), "
                            "set_config('spps.tenant_id', :tid, true)"),
                       {'pid': str(identity[0]), 'tid': str(identity[1])})


def install_identity(factory):
    @event.listens_for(factory, 'after_begin')
    def transaction_started(session, transaction, connection):
        # Reapply after commit/rollback; never retain identity on a pooled connection.
        apply_identity(connection, session.info.get('identity', ('', '')))


def bind_identity(session, user):
    session.info['identity'] = (user.id, user.tenant_id)
    if session.in_transaction():
        apply_identity(session.connection(), session.info['identity'])


def check_role(engine, expected, require_clinical=False):
    with engine.connect() as connection:
        role = connection.execute(text("SELECT current_user, rolsuper, rolbypassrls, rolcreaterole "
                                       "FROM pg_roles WHERE rolname=current_user")).one()
        owned = connection.scalar(text("SELECT count(*) FROM pg_class c JOIN pg_namespace n "
                                       "ON n.oid=c.relnamespace WHERE n.nspname='spps_dev' "
                                       "AND c.relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user)"))
        memberships = connection.scalar(text("SELECT count(*) FROM pg_auth_members "
                                             "WHERE member=(SELECT oid FROM pg_roles WHERE rolname=current_user)"))
        protected = connection.scalar(text("SELECT count(*) FROM pg_class c JOIN pg_namespace n "
                                            "ON n.oid=c.relnamespace WHERE n.nspname='spps_dev' "
                                            "AND c.relkind='r' AND c.relrowsecurity AND c.relforcerowsecurity"))
        # v1 has 33 clinical tables; every v2 clinical table (server/v2/models.py) must also be FORCE RLS.
        from .v2.models import V2Base
        if role[0] != expected or any(role[1:]) or owned or memberships or protected != 33 + len(V2Base.metadata.tables):
            raise RuntimeError('Integration database role or RLS baseline is unsafe')
        if require_clinical:
            required = (
                'own_note_insert', 'own_note_finalize',
                'own_assignment_insert', 'own_report_insert',
                'own_injury_insert', 'own_checkin_insert'
            )
            installed = connection.execute(text(
                "SELECT policyname FROM pg_policies WHERE schemaname='spps_dev' "
                "AND policyname = ANY(:names)"
            ), {'names': list(required)}).scalars().all()
            if set(installed) != set(required):
                raise RuntimeError('Local clinical-parity RLS policies are missing; refusing to open clinical writes')
