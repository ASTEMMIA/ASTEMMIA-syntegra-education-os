-- Generated from the Python domain model by scripts/generate_ddl.py. Do not edit.
CREATE DATABASE IF NOT EXISTS att;

CREATE TABLE att.school_closure (
	on_date DATE NOT NULL, 
	reason VARCHAR(120) NOT NULL, 
	tenant_id BINARY(16) NOT NULL, 
	scope_node_id BINARY(16) NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BINARY(16), 
	updated_by BINARY(16), 
	row_version INTEGER NOT NULL, 
	source_system VARCHAR(64), 
	source_ref VARCHAR(128), 
	id BINARY(16) NOT NULL, 
	CONSTRAINT pk_school_closure PRIMARY KEY (id), 
	CONSTRAINT uq_school_closure_tenant_id_scope_node_id_on_date UNIQUE (tenant_id, scope_node_id, on_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE att.staff_attendance (
	staff_id BINARY(16) NOT NULL, 
	institution_id BINARY(16) NOT NULL, 
	on_date DATE NOT NULL, 
	status VARCHAR(14) NOT NULL, 
	time_in TIME, 
	time_out TIME, 
	note VARCHAR(200), 
	self_recorded BOOL NOT NULL, 
	recorded_by BINARY(16), 
	tenant_id BINARY(16) NOT NULL, 
	scope_node_id BINARY(16) NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BINARY(16), 
	updated_by BINARY(16), 
	row_version INTEGER NOT NULL, 
	source_system VARCHAR(64), 
	source_ref VARCHAR(128), 
	id BINARY(16) NOT NULL, 
	CONSTRAINT pk_staff_attendance PRIMARY KEY (id), 
	CONSTRAINT uq_staff_attendance_tenant_id_staff_id_on_date UNIQUE (tenant_id, staff_id, on_date), 
	CONSTRAINT ck_staff_attendance_status CHECK (status IN ('present','late','absent','leave','sick','official_duty')), 
	CONSTRAINT fk_staff_attendance_staff_id_staff FOREIGN KEY(staff_id) REFERENCES acad.staff (id), 
	CONSTRAINT fk_staff_attendance_institution_id_institution FOREIGN KEY(institution_id) REFERENCES org.institution (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_staff_attendance_tenant_id_institution_id_on_date ON att.staff_attendance (tenant_id, institution_id, on_date);

CREATE TABLE att.register (
	institution_id BINARY(16) NOT NULL, 
	class_group_id BINARY(16) NOT NULL, 
	term_id BINARY(16) NOT NULL, 
	on_date DATE NOT NULL, 
	session VARCHAR(2) NOT NULL, 
	taken_by BINARY(16), 
	taken_at DATETIME(6) NOT NULL, 
	tenant_id BINARY(16) NOT NULL, 
	scope_node_id BINARY(16) NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BINARY(16), 
	updated_by BINARY(16), 
	row_version INTEGER NOT NULL, 
	source_system VARCHAR(64), 
	source_ref VARCHAR(128), 
	id BINARY(16) NOT NULL, 
	CONSTRAINT pk_register PRIMARY KEY (id), 
	CONSTRAINT uq_register_tenant_id_class_group_id_on_date_session UNIQUE (tenant_id, class_group_id, on_date, session), 
	CONSTRAINT ck_register_session CHECK (session IN ('am','pm')), 
	CONSTRAINT fk_register_institution_id_institution FOREIGN KEY(institution_id) REFERENCES org.institution (id), 
	CONSTRAINT fk_register_class_group_id_class_group FOREIGN KEY(class_group_id) REFERENCES acad.class_group (id), 
	CONSTRAINT fk_register_term_id_academic_period FOREIGN KEY(term_id) REFERENCES acad.academic_period (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_register_tenant_id_institution_id_on_date ON att.register (tenant_id, institution_id, on_date);

CREATE TABLE att.learner_mark (
	register_id BINARY(16) NOT NULL, 
	learner_id BINARY(16) NOT NULL, 
	enrolment_id BINARY(16) NOT NULL, 
	status VARCHAR(8) NOT NULL, 
	reason VARCHAR(200), 
	tenant_id BINARY(16) NOT NULL, 
	scope_node_id BINARY(16) NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	created_by BINARY(16), 
	updated_by BINARY(16), 
	row_version INTEGER NOT NULL, 
	source_system VARCHAR(64), 
	source_ref VARCHAR(128), 
	id BINARY(16) NOT NULL, 
	CONSTRAINT pk_learner_mark PRIMARY KEY (id), 
	CONSTRAINT uq_learner_mark_tenant_id_register_id_learner_id UNIQUE (tenant_id, register_id, learner_id), 
	CONSTRAINT ck_learner_mark_status CHECK (status IN ('present','late','absent','excused')), 
	CONSTRAINT fk_learner_mark_register_id_register FOREIGN KEY(register_id) REFERENCES att.register (id), 
	CONSTRAINT fk_learner_mark_learner_id_learner FOREIGN KEY(learner_id) REFERENCES acad.learner (id), 
	CONSTRAINT fk_learner_mark_enrolment_id_enrolment FOREIGN KEY(enrolment_id) REFERENCES acad.enrolment (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_learner_mark_tenant_id_learner_id ON att.learner_mark (tenant_id, learner_id);

CREATE TABLE att.attendance_correction (
	id BINARY(16) NOT NULL, 
	tenant_id BINARY(16) NOT NULL, 
	mark_id BINARY(16) NOT NULL, 
	old_status VARCHAR(8) NOT NULL, 
	new_status VARCHAR(8) NOT NULL, 
	reason VARCHAR(200) NOT NULL, 
	by_user BINARY(16), 
	at DATETIME(6) NOT NULL, 
	CONSTRAINT pk_attendance_correction PRIMARY KEY (id), 
	CONSTRAINT fk_attendance_correction_mark_id_learner_mark FOREIGN KEY(mark_id) REFERENCES att.learner_mark (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX ix_attendance_correction_tenant_id_mark_id ON att.attendance_correction (tenant_id, mark_id);
