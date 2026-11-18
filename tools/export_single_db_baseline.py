"""Emit current-model PostgreSQL baseline SQL for isolated, empty SPPS schemas.

Do not execute directly: review against privileged Supabase schema snapshot.
This has no database connection or credentials.
"""
import base64
from sqlalchemy.schema import CreateTable, CreateIndex
from sqlalchemy.dialects.postgresql import dialect
from server.db import Base
from server import models  # noqa
from server.platform_identity_models import IdentityBase

def commands(metadata, label):
    tables=list(metadata.sorted_tables)
    print(f"SPPS_META:{label}_TABLES={len(tables)}")
    for table in tables:
        stmt=str(CreateTable(table, if_not_exists=True).compile(dialect=dialect()))
        yield stmt+";"
    for table in tables:
        for idx in sorted(table.indexes, key=lambda i:i.name):
            yield str(CreateIndex(idx, if_not_exists=True).compile(dialect=dialect()))+";"

clinical=list(commands(Base.metadata,"CLINICAL"))
identity=list(commands(IdentityBase.metadata,"IDENTITY"))
payload="BEGIN;\nSET LOCAL search_path=spps_dev;\n"+"\n".join(clinical+identity)+"\nCOMMIT;\n"
encoded=base64.b64encode(payload.encode("utf-8")).decode("ascii")
print("SPPS_DDL_START")
for i in range(0,len(encoded),120):
    print("SPPS_DDL_CHUNK:"+encoded[i:i+120])
print("SPPS_DDL_END")
