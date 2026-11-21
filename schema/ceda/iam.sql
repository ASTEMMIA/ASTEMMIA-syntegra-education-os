-- Generated from the Python domain model by scripts/generate_ddl.py. Do not edit.
CREATE DATABASE IF NOT EXISTS iam;

CREATE TABLE iam.app_user (
	id BINARY(16) NOT NULL, 
	tenant_id BINARY(16) NOT NULL, 
	person_id BINARY(16), 
	login VARCHAR(255) NOT NULL, 
	display_name VARCHAR(255) NOT NULL, 
	idp_subject VARCHAR(255), 
	status VARCHAR(16) NOT NULL, 
	mfa_required BOOL NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	CONSTRAINT pk_app_user PRIMARY KEY (id), 
	CONSTRAINT uq_app_user_tenant_id_login UNIQUE (tenant_id, login), 
	CONSTRAINT uq_app_user_idp_subject UNIQUE (idp_subject)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE iam.`role` (
	id BINARY(16) NOT NULL, 
	tenant_id BINARY(16), 
	code VARCHAR(32) NOT NULL, 
	name VARCHAR(255) NOT NULL, 
	builtin BOOL NOT NULL, 
	clearance INTEGER NOT NULL, 
	cloned_from_id BINARY(16), 
	CONSTRAINT pk_role PRIMARY KEY (id), 
	CONSTRAINT uq_role_tenant_id_code UNIQUE (tenant_id, code), 
	CONSTRAINT ck_role_clearance CHECK (clearance BETWEEN 1 AND 5), 
	CONSTRAINT fk_role_cloned_from_id_role FOREIGN KEY(cloned_from_id) REFERENCES iam.`role` (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE iam.access_grant (
	id BINARY(16) NOT NULL, 
	tenant_id BINARY(16) NOT NULL, 
	user_id BINARY(16) NOT NULL, 
	role_id BINARY(16) NOT NULL, 
	scope_node_id BINARY(16) NOT NULL, 
	clearance_cap INTEGER NOT NULL, 
	valid_from DATE NOT NULL, 
	valid_to DATE, 
	granted_by BINARY(16), 
	created_at DATETIME(6) NOT NULL, 
	revoked_at DATETIME(6), 
	CONSTRAINT pk_access_grant PRIMARY KEY (id), 
	CONSTRAINT ck_access_grant_clearance_cap CHECK (clearance_cap BETWEEN 1 AND 5), 
	CONSTRAINT fk_access_grant_user_id_app_user FOREIGN KEY(user_id) REFERENCES iam.app_user (id), 
	CONSTRAINT fk_access_grant_role_id_role FOREIGN KEY(role_id) REFERENCES iam.`role` (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_access_grant_tenant_id_user_id ON iam.access_grant (tenant_id, user_id);

CREATE TABLE iam.role_permission (
	role_id BINARY(16) NOT NULL, 
	permission VARCHAR(64) NOT NULL, 
	cell VARCHAR(1) NOT NULL, 
	CONSTRAINT pk_role_permission PRIMARY KEY (role_id, permission), 
	CONSTRAINT ck_role_permission_cell CHECK (cell IN ('Y','P','C','A')), 
	CONSTRAINT fk_role_permission_role_id_role FOREIGN KEY(role_id) REFERENCES iam.`role` (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
