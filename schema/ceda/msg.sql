-- Generated from the Python domain model by scripts/generate_ddl.py. Do not edit.
CREATE DATABASE IF NOT EXISTS msg;

CREATE TABLE msg.conversation (
	id BINARY(16) NOT NULL, 
	tenant_id BINARY(16) NOT NULL, 
	scope_node_id BINARY(16) NOT NULL, 
	kind VARCHAR(12) NOT NULL, 
	title VARCHAR(120), 
	comm_class VARCHAR(14) NOT NULL, 
	posting VARCHAR(6) NOT NULL, 
	institution_id BINARY(16), 
	class_group_id BINARY(16), 
	direct_key VARCHAR(73), 
	next_seq INTEGER NOT NULL, 
	last_message_at DATETIME(6), 
	created_by BINARY(16), 
	created_at DATETIME(6) NOT NULL, 
	CONSTRAINT pk_conversation PRIMARY KEY (id), 
	CONSTRAINT uq_conversation_tenant_id_direct_key UNIQUE (tenant_id, direct_key), 
	CONSTRAINT ck_conversation_kind CHECK (kind IN ('direct','group','class','staff','authority','announcement')), 
	CONSTRAINT ck_conversation_comm_class CHECK (comm_class IN ('institutional','protected')), 
	CONSTRAINT ck_conversation_posting CHECK (posting IN ('all','admins')), 
	CONSTRAINT fk_conversation_institution_id_institution FOREIGN KEY(institution_id) REFERENCES org.institution (id), 
	CONSTRAINT fk_conversation_class_group_id_class_group FOREIGN KEY(class_group_id) REFERENCES acad.class_group (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_conversation_tenant_id_last_message_at ON msg.conversation (tenant_id, last_message_at);

CREATE TABLE msg.conversation_access (
	id BINARY(16) NOT NULL, 
	tenant_id BINARY(16) NOT NULL, 
	conversation_id BINARY(16) NOT NULL, 
	user_id BINARY(16) NOT NULL, 
	purpose VARCHAR(255) NOT NULL, 
	at DATETIME(6) NOT NULL, 
	CONSTRAINT pk_conversation_access PRIMARY KEY (id), 
	CONSTRAINT fk_conversation_access_conversation_id_conversation FOREIGN KEY(conversation_id) REFERENCES msg.conversation (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_conversation_access_tenant_id_conversation_id ON msg.conversation_access (tenant_id, conversation_id);

CREATE TABLE msg.conversation_member (
	id BINARY(16) NOT NULL, 
	tenant_id BINARY(16) NOT NULL, 
	conversation_id BINARY(16) NOT NULL, 
	user_id BINARY(16) NOT NULL, 
	`role` VARCHAR(10) NOT NULL, 
	derived BOOL NOT NULL, 
	joined_at DATETIME(6) NOT NULL, 
	left_at DATETIME(6), 
	last_read_seq INTEGER NOT NULL, 
	muted BOOL NOT NULL, 
	CONSTRAINT pk_conversation_member PRIMARY KEY (id), 
	CONSTRAINT uq_conversation_member_tenant_id_conversation_id_user_id UNIQUE (tenant_id, conversation_id, user_id), 
	CONSTRAINT ck_conversation_member_role CHECK (role IN ('owner','admin','member','read_only')), 
	CONSTRAINT fk_conversation_member_conversation_id_conversation FOREIGN KEY(conversation_id) REFERENCES msg.conversation (id), 
	CONSTRAINT fk_conversation_member_user_id_app_user FOREIGN KEY(user_id) REFERENCES iam.app_user (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_conversation_member_tenant_id_user_id ON msg.conversation_member (tenant_id, user_id);

CREATE TABLE msg.message (
	id BINARY(16) NOT NULL, 
	tenant_id BINARY(16) NOT NULL, 
	conversation_id BINARY(16) NOT NULL, 
	seq INTEGER NOT NULL, 
	sender_user_id BINARY(16), 
	sent_at DATETIME(6) NOT NULL, 
	kind VARCHAR(6) NOT NULL, 
	body_enc MEDIUMBLOB, 
	reply_to_id BINARY(16), 
	edited_at DATETIME(6), 
	deleted_at DATETIME(6), 
	deleted_by BINARY(16), 
	CONSTRAINT pk_message PRIMARY KEY (id), 
	CONSTRAINT uq_message_tenant_id_conversation_id_seq UNIQUE (tenant_id, conversation_id, seq), 
	CONSTRAINT ck_message_kind CHECK (kind IN ('text','file','system')), 
	CONSTRAINT fk_message_conversation_id_conversation FOREIGN KEY(conversation_id) REFERENCES msg.conversation (id), 
	CONSTRAINT fk_message_reply_to_id_message FOREIGN KEY(reply_to_id) REFERENCES msg.message (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE msg.message_attachment (
	id BINARY(16) NOT NULL, 
	tenant_id BINARY(16) NOT NULL, 
	message_id BINARY(16) NOT NULL, 
	blob_key VARCHAR(200) NOT NULL, 
	sha256 VARCHAR(64) NOT NULL, 
	size_bytes INTEGER NOT NULL, 
	mime VARCHAR(100) NOT NULL, 
	name_enc BLOB(1024) NOT NULL, 
	scan_status VARCHAR(12) NOT NULL, 
	CONSTRAINT pk_message_attachment PRIMARY KEY (id), 
	CONSTRAINT uq_message_attachment_tenant_id_blob_key UNIQUE (tenant_id, blob_key), 
	CONSTRAINT ck_message_attachment_scan_status CHECK (scan_status IN ('not_scanned','clean','infected')), 
	CONSTRAINT fk_message_attachment_message_id_message FOREIGN KEY(message_id) REFERENCES msg.message (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_message_attachment_tenant_id_message_id ON msg.message_attachment (tenant_id, message_id);

CREATE TABLE msg.message_edit (
	id BINARY(16) NOT NULL, 
	tenant_id BINARY(16) NOT NULL, 
	message_id BINARY(16) NOT NULL, 
	body_enc MEDIUMBLOB NOT NULL, 
	edited_at DATETIME(6) NOT NULL, 
	CONSTRAINT pk_message_edit PRIMARY KEY (id), 
	CONSTRAINT fk_message_edit_message_id_message FOREIGN KEY(message_id) REFERENCES msg.message (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_message_edit_tenant_id_message_id ON msg.message_edit (tenant_id, message_id);

CREATE TABLE msg.message_reaction (
	id BINARY(16) NOT NULL, 
	tenant_id BINARY(16) NOT NULL, 
	message_id BINARY(16) NOT NULL, 
	user_id BINARY(16) NOT NULL, 
	emoji VARCHAR(8) NOT NULL, 
	CONSTRAINT pk_message_reaction PRIMARY KEY (id), 
	CONSTRAINT uq_message_reaction_tenant_id_message_id_user_id_emoji UNIQUE (tenant_id, message_id, user_id, emoji), 
	CONSTRAINT fk_message_reaction_message_id_message FOREIGN KEY(message_id) REFERENCES msg.message (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE msg.message_report (
	id BINARY(16) NOT NULL, 
	tenant_id BINARY(16) NOT NULL, 
	scope_node_id BINARY(16) NOT NULL, 
	message_id BINARY(16) NOT NULL, 
	reporter_user_id BINARY(16) NOT NULL, 
	reason VARCHAR(500) NOT NULL, 
	status VARCHAR(10) NOT NULL, 
	handled_by BINARY(16), 
	handled_at DATETIME(6), 
	note VARCHAR(500), 
	created_at DATETIME(6) NOT NULL, 
	CONSTRAINT pk_message_report PRIMARY KEY (id), 
	CONSTRAINT ck_message_report_status CHECK (status IN ('open','dismissed','actioned')), 
	CONSTRAINT fk_message_report_message_id_message FOREIGN KEY(message_id) REFERENCES msg.message (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_message_report_tenant_id_status ON msg.message_report (tenant_id, status);
