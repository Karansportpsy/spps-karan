"""SPPS Version 1.0 - Free database engine and session set-up."""
import os
import re
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

class Base(DeclarativeBase):
    pass

def make_engine(url=None, *, pool=None):
    """``pool=(size, overflow)`` overrides the default bounded pool (used for the v2 audit writer)."""
    url = url or os.getenv('DATABASE_URL', 'sqlite:///./data/model_c.db')
    if url.startswith('postgres://'):
        url = 'postgresql+psycopg://' + url[len('postgres://'):]
    if url.startswith('postgresql://'):
        url = 'postgresql+psycopg://' + url[len('postgresql://'):]
    options = {'check_same_thread': False} if url.startswith('sqlite') else {}
    if os.getenv('MODEL_C_INTEGRATION_DEV', 'false').lower() == 'true':
        from .identity import require_local_database
        require_local_database(url)
        options['options'] = '-c search_path=spps_dev'
    if os.getenv('MODEL_C_ENV','local').lower() in ('staging','production') and \
            os.getenv('SPPS_HOSTED_RLS','false').lower()=='true':
        # Hosted RLS profile: URL already validated by server.hosted_profile; TLS can only be strengthened.
        from .hosted_profile import (CLINICAL_SCHEMA, IDENTITY_SCHEMA, connect_args, normalize,
                                     IDENTITY_AUTH_ROLE, IDENTITY_PORTAL_ROLE)
        parsed=normalize(url)
        db_principal=(parsed.username or "").split(".",1)[0]
        selected_schema=(IDENTITY_SCHEMA if db_principal in
                         (IDENTITY_AUTH_ROLE, IDENTITY_PORTAL_ROLE) else CLINICAL_SCHEMA)
        options.update(connect_args(parsed, schema=selected_schema))
    elif os.getenv('MODEL_C_ENV','local').lower() in ('staging','production'):
        if not url.startswith('postgresql+psycopg://'):
            raise RuntimeError('Hosted database requires postgresql+psycopg:// connection')
        # Do not allow plaintext DB connections in hosted mode. The pooler can
        # terminate TLS; use a trusted Supabase/managed PostgreSQL endpoint.
        options['sslmode']='require'
        schema=os.getenv('MODEL_C_DB_SCHEMA','spps_pilot')
        if not re.fullmatch(r'[a-z][a-z0-9_]{2,62}', schema):
            raise RuntimeError('Invalid PostgreSQL schema name')
        if schema=='public':
            raise RuntimeError('Pilot data must use a private, unexposed PostgreSQL schema')
        options['options']=f'-c search_path={schema}'
    # Small bounded pools: Render free tier can host four isolated roles.
    # Keep independent least-privilege connections while avoiding 4 x 5 DB sessions.
    hosted = os.getenv('MODEL_C_ENV','local').lower() in ('staging','production')
    engine = create_engine(url, connect_args=options, pool_pre_ping=True,
                           pool_size=pool[0] if pool else (1 if hosted else 3),
                           max_overflow=pool[1] if pool else (1 if hosted else 2),
                           pool_timeout=12 if hosted else 15,
                           pool_recycle=900 if hosted else 1800)
    if engine.dialect.name == 'sqlite':
        @event.listens_for(engine, 'connect')
        def foreign_keys_on(conn, _):
            conn.execute('PRAGMA foreign_keys=ON')
    return engine

def make_session(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
