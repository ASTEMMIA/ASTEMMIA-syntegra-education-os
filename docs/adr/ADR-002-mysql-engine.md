# ADR-002: Transactional store

**Status:** Proposed (revised 2026-11-21). Load and failover testing is still outstanding.

## Decision
**MySQL 9.7 LTS** (9.7.4 at time of writing; supported to April 2034). Driver: `asyncmy` with
`caching_sha2_password` (`mysql_native_password` was removed in MySQL 9). Connections run with
`time_zone='+00:00'` and strict SQL mode.

Hosting: **Amazon RDS for MySQL 9.7 Community, Multi-AZ DB cluster** (one writer plus two readable
standbys across three AZs). This changes the CEDA §11 default:

| | Aurora MySQL (CEDA default) | RDS for MySQL 9.7 (this ADR) |
|---|---|---|
| Newest MySQL available | **8.4** | **9.7 LTS** |
| HA | 6-way storage across 3 AZs, ~30 s failover | Multi-AZ cluster, typically under 35 s failover, 2 readable standbys |
| Read scale | Up to 15 replicas | Standbys plus read replicas |
| Cross-region DR | Aurora Global Database | Cross-region read replica, AWS Backup copies |

Choosing the latest MySQL rules out Aurora until AWS ships Aurora 9.x. The CEDA already lists RDS for
MySQL as the evaluated alternative (§11), and its HA and DR targets (§15) must be re-validated on RDS.

**MySQL 26.x Innovation releases** (26.10.0 now) are **not for production**: each one is supported
only until the next. CI runs them as an early-warning job so the move to the next LTS is cheap.

## Evidence so far
* Locally, the full suite, Alembic migration, daily partitioning of `audit_event` and the 20-writer
  concurrency test pass on MySQL 8.0.46. CI runs the same suite on `mysql:9.7.4-oraclelinux9`
  (Python 3.14 and 3.15) and `mysql:26.10.0-oraclelinux9`.
* The MySQL 9.7.4 server binary was downloaded and its Oracle GPG signature verified, but running it in
  the build sandbox was not permitted. **The first green CI run on 9.7.4 is the acceptance evidence for
  this ADR.**
* Without ORM `relationship()`s, SQLAlchemy does not order inserts across mappers by FK, so services
  flush parents explicitly. SQLite did not catch this; MySQL did.

## Edition
Community Edition on RDS (decided in ADR-006).
