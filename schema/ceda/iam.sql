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
	CONSTRAINT uq_app_user_tenant_id_idp_subject UNIQUE (tenant_id, idp_subject)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE iam.oidc_flow (
	id BINARY(16) NOT NULL, 
	tenant_id BINARY(16) NOT NULL, 
	idp_key VARCHAR(32) NOT NULL, 
	state_hash BINARY(32) NOT NULL, 
	nonce VARCHAR(64) NOT NULL, 
	verifier_enc VARBINARY(512) NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	expires_at DATETIME(6) NOT NULL, 
	used_at DATETIME(6), 
	completed_at DATETIME(6), 
	CONSTRAINT pk_oidc_flow PRIMARY KEY (id), 
	CONSTRAINT uq_oidc_flow_state_hash UNIQUE (state_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_oidc_flow_expires_at ON iam.oidc_flow (expires_at);

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

CREATE TABLE iam.auth_session (
	id BINARY(16) NOT NULL, 
	tenant_id BINARY(16) NOT NULL, 
	user_id BINARY(16) NOT NULL, 
	method VARCHAR(16) NOT NULL, 
	amr VARCHAR(64) NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	absolute_expires_at DATETIME(6) NOT NULL, 
	revoked_at DATETIME(6), 
	revoke_reason VARCHAR(32), 
	ip VARCHAR(45), 
	user_agent VARCHAR(255), 
	CONSTRAINT pk_auth_session PRIMARY KEY (id), 
	CONSTRAINT fk_auth_session_user_id_app_user FOREIGN KEY(user_id) REFERENCES iam.app_user (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_auth_session_tenant_id_user_id ON iam.auth_session (tenant_id, user_id);

CREATE TABLE iam.email_sign_in_code (
	id BINARY(16) NOT NULL, 
	tenant_id BINARY(16) NOT NULL, 
	user_id BINARY(16) NOT NULL, 
	code_hash BINARY(32) NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	expires_at DATETIME(6) NOT NULL, 
	attempts INTEGER NOT NULL, 
	used_at DATETIME(6), 
	ip VARCHAR(45), 
	CONSTRAINT pk_email_sign_in_code PRIMARY KEY (id), 
	CONSTRAINT fk_email_sign_in_code_user_id_app_user FOREIGN KEY(user_id) REFERENCES iam.app_user (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_email_sign_in_code_tenant_id_user_id_created_at ON iam.email_sign_in_code (tenant_id, user_id, created_at);

CREATE TABLE iam.mfa_credential (
	id BINARY(16) NOT NULL, 
	tenant_id BINARY(16) NOT NULL, 
	user_id BINARY(16) NOT NULL, 
	kind VARCHAR(16) NOT NULL, 
	secret_enc VARBINARY(512) NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	confirmed_at DATETIME(6), 
	last_step BIGINT, 
	last_used_at DATETIME(6), 
	failed_attempts INTEGER NOT NULL, 
	locked_until DATETIME(6), 
	CONSTRAINT pk_mfa_credential PRIMARY KEY (id), 
	CONSTRAINT uq_mfa_credential_user_id_kind UNIQUE (user_id, kind), 
	CONSTRAINT ck_mfa_credential_kind CHECK (kind IN ('totp')), 
	CONSTRAINT fk_mfa_credential_user_id_app_user FOREIGN KEY(user_id) REFERENCES iam.app_user (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE iam.recovery_code (
	id BINARY(16) NOT NULL, 
	tenant_id BINARY(16) NOT NULL, 
	user_id BINARY(16) NOT NULL, 
	code_hash BINARY(32) NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	used_at DATETIME(6), 
	CONSTRAINT pk_recovery_code PRIMARY KEY (id), 
	CONSTRAINT fk_recovery_code_user_id_app_user FOREIGN KEY(user_id) REFERENCES iam.app_user (id), 
	CONSTRAINT uq_recovery_code_code_hash UNIQUE (code_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_recovery_code_user_id ON iam.recovery_code (user_id);

CREATE TABLE iam.role_permission (
	role_id BINARY(16) NOT NULL, 
	permission VARCHAR(64) NOT NULL, 
	cell VARCHAR(1) NOT NULL, 
	CONSTRAINT pk_role_permission PRIMARY KEY (role_id, permission), 
	CONSTRAINT ck_role_permission_cell CHECK (cell IN ('Y','P','C','A')), 
	CONSTRAINT fk_role_permission_role_id_role FOREIGN KEY(role_id) REFERENCES iam.`role` (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE iam.refresh_token (
	id BINARY(16) NOT NULL, 
	tenant_id BINARY(16) NOT NULL, 
	session_id BINARY(16) NOT NULL, 
	token_hash BINARY(32) NOT NULL, 
	issued_at DATETIME(6) NOT NULL, 
	expires_at DATETIME(6) NOT NULL, 
	used_at DATETIME(6), 
	replaced_by_id BINARY(16), 
	CONSTRAINT pk_refresh_token PRIMARY KEY (id), 
	CONSTRAINT fk_refresh_token_session_id_auth_session FOREIGN KEY(session_id) REFERENCES iam.auth_session (id), 
	CONSTRAINT uq_refresh_token_token_hash UNIQUE (token_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_refresh_token_tenant_id_session_id ON iam.refresh_token (tenant_id, session_id);
