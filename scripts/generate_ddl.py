"""Generate schema/ceda/<schema>.sql (MySQL 8 DDL) from the Python domain model (CEDA §6).

uv run python scripts/generate_ddl.py          # write files
uv run python scripts/generate_ddl.py --check  # CI: fail if checked-in DDL is stale
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateIndex, CreateTable

import syntegra.academic.models
import syntegra.governance.models
import syntegra.hr.models
import syntegra.iam.models
import syntegra.institutions.models
import syntegra.learners.models
import syntegra.mdm.models
import syntegra.messaging.models
import syntegra.records.models  # noqa: F401
from syntegra.platform.db import SCHEMAS, Base

OUT = Path(__file__).resolve().parent.parent / "schema" / "ceda"
HEADER = "-- Generated from the Python domain model by scripts/generate_ddl.py. Do not edit.\n"


def render() -> dict[str, str]:
    dialect = mysql.dialect()
    by_schema: dict[str, list[str]] = defaultdict(list)
    for table in Base.metadata.sorted_tables:
        schema = table.schema or "default"
        ddl = str(CreateTable(table).compile(dialect=dialect)).strip()
        ddl += " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;"
        by_schema[schema].append(ddl)
        for index in sorted(table.indexes, key=lambda i: i.name or ""):
            by_schema[schema].append(str(CreateIndex(index).compile(dialect=dialect)).strip() + ";")
    files = {}
    for schema in SCHEMAS:
        body = by_schema.get(schema)
        if body:
            files[f"{schema}.sql"] = (
                HEADER + f"CREATE DATABASE IF NOT EXISTS {schema};\n\n" + "\n\n".join(body) + "\n"
            )
    return files


def main() -> int:
    files = render()
    if "--check" in sys.argv:
        stale = [
            n for n, c in files.items() if not (OUT / n).exists() or (OUT / n).read_text() != c
        ]
        if stale:
            print("stale DDL, run scripts/generate_ddl.py:", ", ".join(stale))
            return 1
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (OUT / name).write_text(content)
        print(f"wrote schema/ceda/{name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
