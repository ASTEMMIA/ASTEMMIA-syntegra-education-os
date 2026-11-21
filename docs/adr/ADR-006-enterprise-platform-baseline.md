# ADR-006: Enterprise platform baseline (Linux, Python, MySQL editions)

**Status:** Accepted 2026-11-21. MySQL edition: option 1, RDS for MySQL 9.7 Community.

## Linux
* **Application containers:** Red Hat **UBI 10** (`ubi10/ubi-minimal:10.2`), which is RHEL 10 userspace.
  UBI is freely redistributable and covered by Red Hat support when it runs on RHEL hosts or OpenShift.
  Hosts and worker nodes run **RHEL 10.x** (10.3 is current), hardened to the CIS benchmark (§17).
* **MySQL containers (dev and CI):** the official `mysql:9.7.4-oraclelinux9` image, on Oracle Linux 9 (an
  enterprise Linux and RHEL-compatible). Self-managed production MySQL, if chosen, runs on RHEL 10.

## Python
There is no "Enterprise Edition" of CPython. Enterprise support comes from the OS vendor or a commercial
distribution.
* **Runtime:** **Python 3.14 as built and supported by Red Hat** (`python3.14` from RHEL 10 AppStream,
  3.14.10 now; the RHEL 10 default `python3` is 3.12). `requires-python = ">=3.14"`.
* **Forward compatibility:** CI also tests **Python 3.15** (3.15.2, the newest upstream release, which
  Red Hat does not yet ship). All native dependencies publish 3.15 wheels, and the suite and mypy pass
  on 3.15.2 locally.
* **The Python stack itself:** FastAPI, SQLAlchemy 2, Pydantic v2, Alembic, uvicorn and uvloop, plus ruff,
  mypy strict, import-linter, pytest and coverage. This is the CEDA §10.1 stack, unchanged.
* **Alternatives if a commercial Python vendor is required:** ActiveState Platform or Anaconda
  (indemnified builds, SBOMs, CVE SLAs). They need a licence and would replace only the Python base
  layer in the Dockerfile.

## MySQL edition: decided (option 1)
**MySQL Enterprise Edition** is the same server plus licensed components. Each maps to CEDA requirements:

| Enterprise component | CEDA need | Community / RDS equivalent |
|---|---|---|
| Transparent Data Encryption + Oracle Key Vault/KMS keyring | §8.1 encryption at rest | RDS storage encryption with AWS KMS |
| MySQL Enterprise Audit | §9.1 database-level audit (complements app audit chain) | RDS option group: MariaDB Audit Plugin |
| Enterprise Firewall | §17 OWASP ASVS L2 defence in depth | App-layer allow-lists, WAF, no raw SQL (lint) |
| Thread Pool | §17 50k concurrent users per region | RDS Proxy plus app connection pooling |
| Enterprise Backup | §11.4 backup and restore | RDS PITR (35 days), AWS Backup vault lock |
| Data Masking | §8.1 masked display | Already done in the app (`core.ids.mask`) |

**Amazon RDS (and Aurora) offer Community Edition only.** You can have the latest MySQL with
Enterprise Edition, or managed RDS, but not both:

1. **RDS for MySQL 9.7, Community. CHOSEN.** Fully managed, with the AWS equivalents above (KMS
   encryption at rest, MariaDB Audit Plugin option group, RDS Proxy, PITR plus AWS Backup vault lock).
   Lowest operational load, and every CEDA requirement is still met.
2. **MySQL HeatWave on AWS.** Oracle-managed, Enterprise features, latest versions. It is a different
   operator and contract, and its residency regions must cover the tenant regions (§2).
3. **Self-managed MySQL Enterprise 9.7 on RHEL 10 EC2.** Full Enterprise features, but we own HA (InnoDB
   Cluster), patching, backup and DR. Needs Oracle MySQL Enterprise subscriptions.

The application code is the same for all three options. Only Terraform and the runbooks differ.

## Consequences of the decision
* Terraform (Phase 0 infrastructure track) provisions an RDS for MySQL 9.7 Multi-AZ DB cluster with storage
  encryption (customer-managed KMS key), the audit plugin option group, RDS Proxy, Performance Insights,
  35-day PITR and AWS Backup copies to the DR region and backup account.
* No Oracle MySQL Enterprise subscription is required.
* Options 2 and 3 can be revisited by a new ADR without application changes.
