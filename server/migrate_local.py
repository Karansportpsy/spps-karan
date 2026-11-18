"""Non-destructive upgrade of a local SQLite development database to the current schema.
PostgreSQL changes require separately reviewed SQL migration; this helper intentionally never alters remote databases.
"""
from sqlalchemy import inspect,text,select


def migrate_local(engine):
    if engine.dialect.name!='sqlite':return []
    inspector=inspect(engine)
    if 'practitioners' not in inspector.get_table_names():return []
    have={col['name'] for col in inspector.get_columns('practitioners')}
    changes=[]
    with engine.begin() as connection:
        if 'tier' not in have:
            connection.execute(text("ALTER TABLE practitioners ADD COLUMN tier VARCHAR(16) NOT NULL DEFAULT 'core'"))
            changes.append('practitioners.tier')
        if 'is_admin' not in have:
            connection.execute(text('ALTER TABLE practitioners ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0'))
            changes.append('practitioners.is_admin')
    if 'practitioner_profiles' in inspector.get_table_names():
        profile_cols={c['name'] for c in inspector.get_columns('practitioner_profiles')}
        if 'avatar_key' not in profile_cols:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE practitioner_profiles ADD COLUMN avatar_key VARCHAR(32) NOT NULL DEFAULT 'portrait_01'"))
            changes.append('practitioner_profiles.avatar_key')
    # Existing profile rows get new registration fields without touching other columns.
    if 'practitioner_profiles' in inspector.get_table_names():
        cols={c['name'] for c in inspector.get_columns('practitioner_profiles')}
        additions={'consultation_fee_paise':'INTEGER NOT NULL DEFAULT 150000','avatar_bytes':'BLOB','credentials_verified':'BOOLEAN NOT NULL DEFAULT 0'}
        with engine.begin() as connection:
            for col,ddl in additions.items():
                if col not in cols:
                    connection.execute(text(f'ALTER TABLE practitioner_profiles ADD COLUMN {col} {ddl}'))
                    changes.append('practitioner_profiles.'+col)
    # Scope historical enquiries and custom monitoring to their owner where known.
    # Legacy unassigned enquiries remain visible only to the administrator.
    for tbl,col,ddl in (('inquiries','practitioner_id','INTEGER'),('factors','owner_practitioner_id','INTEGER')):
        if tbl in inspector.get_table_names():
            cols={c['name'] for c in inspector.get_columns(tbl)}
            if col not in cols:
                with engine.begin() as connection:
                    connection.execute(text(f'ALTER TABLE {tbl} ADD COLUMN {col} {ddl}'))
                changes.append(tbl+'.'+col)
    # Legacy Free/Plus accounts become Core; access still requires an active subscription.
    # SQLite local migration only; no live database is modified.
    with engine.begin() as connection:
        connection.execute(text("UPDATE practitioners SET tier='core' WHERE tier IN ('plus','free')"))
    return changes
