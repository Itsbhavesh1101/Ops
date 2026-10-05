# Company MySQL Schema Inventory

---

## 1. Purpose and Scope
This document establishes the authoritative technical inventory and domain structural analysis of the **IR Infotech Company MySQL Database** (exported under `iredu`) for the **IR Infotech E-7 Operations Engine** under **Task 0G.1**.

### 1.1 Context & Objectives
* **External Operational Source**: The company MySQL database represents the pre-existing operational Enterprise Resource Planning (ERP) and Student Information System (SIS) utilized across client institutions.
* **E-7 Ingestion Boundary**: The E-7 Operations Engine will consume relevant operational entities (staff, students, class sections, subjects, historical timetables, academic calendars) through dedicated ingestion adapters.
* **Clean-Room & Governance Discipline**: In strict compliance with [`docs/SOURCE_OF_TRUTH.md`](file:///F:/operations-engine/docs/SOURCE_OF_TRUTH.md) and [`docs/IMPLEMENTATION_GUARDRAILS.md`](file:///F:/operations-engine/docs/IMPLEMENTATION_GUARDRAILS.md), this analysis is conducted on DDL schema definitions only. Zero production code, zero ORM models, zero application database migrations, and zero runtime dependencies have been introduced.
* **PostgreSQL SoR Invariant**: This inventory confirms that the existence of the company MySQL database does **NOT** alter the architectural decision that **E-7 PostgreSQL is the single canonical System of Record (SoR)** for all E-7-owned domain entities (Goals, Plans, Tasks, Milestones, Task Dependencies, Workflow Mappings, Constraint Schedules, Outbox Events, and Audit Trails).

---

## 2. Analysis Method
The inventory was conducted via automated, deterministic parsing of the structure-only MySQL Workbench DDL export:
1. **Grammar & AST Parsing**: Each `.sql` export file was parsed to extract `CREATE TABLE` statements, column specifications, primary keys, foreign key constraints, table options, and index declarations.
2. **Referential Graph Traversal**: Bidirectional foreign key dependencies were mapped across all tables, identifying central hubs, leaf tables, and isolated schema clusters.
3. **Sensitive Surface Screening**: Every column name and definition was classified according to data protection standards (identifying authentication secrets, API credentials, banking details, payroll data, government identifiers, biometric fields, and PII).
4. **Domain Categorization & Relevance Mapping**: Tables were objectively categorized based on schema evidence and evaluated for preliminary relevance to E-7 engine subsystems (Task Intelligence, Workflow, Scheduling, Resource Management, Calendar Integrations, Reporting).
5. **Strict No-Hallucination Separation**: All observations maintain a strict distinction between **SCHEMA FACTS** (proven by DDL definitions), **ENGINEERING INFERENCES** (logical domain associations), and **BUSINESS CONFIRMATION ITEMS** (unresolved operational semantics).

---

## 3. Source Boundary
> [!IMPORTANT]
> **STRUCTURE-ONLY EXPORT INSPECTION — NO LIVE CONNECTION**  
> * **Source Location**: `C:\\Users\\barma\\OneDrive\\ドキュメント\\dumps\\Dump20260924` (247 schema `.sql` files exported from MySQL server version `8.0.35`, database `iredu`).
> * **Zero Live Queries**: Zero connections were made to the live MySQL database server.
> * **Zero Credentials**: No database passwords, access keys, or user tokens were inspected or requested.
> * **Zero Modifying Operations**: All source SQL files remain completely unaltered.

### 3.1 Repository Change-Control & Integrity Verification Note
* **Repository Git State**: `F:\operations-engine` currently has no discoverable `.git` repository according to Task 0G.1.
* **Verification Scope**: Git HEAD and `git status` based integrity verification was therefore unavailable.
* **Filesystem Verification**: Direct filesystem and change-scope verification was employed instead to confirm repository clean-room invariants.
* **Governance Clarification**: Git validation was not claimed to have passed; integrity was confirmed via direct directory structure and timestamp auditing. This is a repository-governance clarification, not an implementation blocker for the schema inventory.

---

## 4. Schema Statistics

| Metric | Value | Architectural Interpretation |
| :--- | :---: | :--- |
| **Total SQL Files Processed** | `247` | Exactly one `.sql` file per table export. |
| **Total `CREATE TABLE` Statements** | `247` | 1:1 parity between files and table definitions. |
| **Total Tables Discovered** | `247` | Complete coverage of institutional ERP schema. |
| **Total Columns Defined** | `2637` | Average of 10.68 columns per table. |
| **Total Primary Keys** | `246` | 246 tables declare an explicit primary key (predominantly auto-increment integer `id`). |
| **Tables Lacking Primary Key** | `1` | Exactly 1 table lacks a PK: [`migrations`](file:///F:/operations-engine/docs/integrations/MYSQL_SCHEMA_INVENTORY.md#migrations). |
| **Total Foreign Key Constraints** | `307` | Explicit relational constraints declared in DDL. |
| **FK Resolution Integrity** | `100.0%` | All 307 foreign key references point exclusively to tables discovered within the 247 export files. Zero dangling external references. |
| **Tables Lacking Foreign Keys** | `109` | 109 tables rely on application-level joins or are standalone lookup/configuration tables. |
| **Completely Isolated Tables** | `66` | 66 tables have neither inbound nor outbound foreign keys. |

---

## 5. Domain-Level Table Inventory
Exhaustive inventory of all 247 discovered MySQL tables:

| Table | Category | PK | FK Count | Primary References | Preliminary E-7 Relevance | Sensitivity | Confidence |
| :--- | :--- | :--- | :---: | :--- | :--- | :--- | :---: |
| **`addon_versions`** | configuration | `id` | 1 | addons | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`addons`** | configuration | `id` | 0 | none | None/Unknown | PERSONAL DATA | MEDIUM |
| **`alumni_events`** | students | `id` | 2 | classes, sessions | Calendar | PERSONAL DATA | HIGH |
| **`alumni_students`** | students | `id` | 1 | students | None/Unknown | PERSONAL DATA | HIGH |
| **`annual_calendar`** | calendar/events | `id` | 1 | sessions | Task Intelligence*, Calendar | INTERNAL BUSINESS DATA | HIGH |
| **`attendence_type`** | attendance | `id` | 0 | none | Reporting | INTERNAL BUSINESS DATA | HIGH |
| **`aws_s3_settings`** | configuration | `id` | 0 | none | Calendar | HIGHLY SENSITIVE / RESTRICTED | MEDIUM |
| **`behaviour_settings`** | configuration | `id` | 0 | none | None/Unknown | INTERNAL BUSINESS DATA | MEDIUM |
| **`book_issues`** | library | `id` | 2 | books, libarary_members | Workflow | INTERNAL BUSINESS DATA | HIGH |
| **`books`** | library | `id` | 0 | none | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`captcha`** | authentication/security | `id` | 0 | none | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`categories`** | configuration | `id` | 0 | none | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`cbse_exam_assessment_types`** | examination | `id` | 1 | cbse_exam_assessments | Scheduling | INTERNAL BUSINESS DATA | HIGH |
| **`cbse_exam_assessments`** | examination | `id` | 0 | none | Scheduling | INTERNAL BUSINESS DATA | HIGH |
| **`cbse_exam_class_sections`** | examination | `id` | 2 | cbse_exams, class_sections | Scheduling | PUBLIC/NON-SENSITIVE METADATA | HIGH |
| **`cbse_exam_grades`** | examination | `id` | 0 | none | Scheduling | INTERNAL BUSINESS DATA | HIGH |
| **`cbse_exam_grades_range`** | examination | `id` | 1 | cbse_exam_grades | Scheduling | INTERNAL BUSINESS DATA | HIGH |
| **`cbse_exam_observations`** | examination | `id` | 0 | none | Scheduling | INTERNAL BUSINESS DATA | HIGH |
| **`cbse_exam_student_subject_rank`** | students | `id` | 3 | cbse_template, student_session, subjects | Scheduling, Reporting | INTERNAL BUSINESS DATA | HIGH |
| **`cbse_exam_students`** | students | `id` | 2 | cbse_exams, student_session | Scheduling | PERSONAL DATA | HIGH |
| **`cbse_exam_timetable`** | examination | `id` | 2 | cbse_exams, subjects | Scheduling | INTERNAL BUSINESS DATA | HIGH |
| **`cbse_exam_timetable_assessment_types`** | examination | `id` | 2 | cbse_exam_assessment_types, cbse_exam_timetable | Scheduling | PUBLIC/NON-SENSITIVE METADATA | HIGH |
| **`cbse_exams`** | examination | `id` | 4 | cbse_exam_assessments, cbse_exam_grades, cbse_terms (+1) | Task Intelligence*, Scheduling | PERSONAL DATA | HIGH |
| **`cbse_marksheet_type`** | examination | `id` | 0 | none | Reporting | INTERNAL BUSINESS DATA | HIGH |
| **`cbse_observation_class_section`** | examination | `id` | 0 | none | None/Unknown | PUBLIC/NON-SENSITIVE METADATA | HIGH |
| **`cbse_observation_parameters`** | examination | `id` | 0 | none | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`cbse_observation_subparameter`** | examination | `id` | 2 | cbse_exam_observations, cbse_observation_parameters | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`cbse_observation_term_student_subparameter`** | students | `id` | 3 | cbse_observation_subparameter, cbse_observation_terms, student_session | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`cbse_observation_terms`** | examination | `id` | 3 | cbse_exam_observations, cbse_terms, sessions | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`cbse_student_exam_ranks`** | students | `id` | 2 | cbse_exams, student_session | Scheduling, Reporting | INTERNAL BUSINESS DATA | HIGH |
| **`cbse_student_subject_marks`** | students | `id` | 4 | cbse_exam_assessment_types, cbse_exam_students, cbse_exam_timetable (+1) | Reporting | PERSONAL DATA | HIGH |
| **`cbse_student_subject_result`** | students | `id` | 0 | none | Reporting | PERSONAL DATA | HIGH |
| **`cbse_student_template_rank`** | students | `id` | 2 | cbse_template, student_session | Reporting | INTERNAL BUSINESS DATA | HIGH |
| **`cbse_template`** | configuration | `id` | 3 | cbse_exams, sessions | None/Unknown | PERSONAL DATA | HIGH |
| **`cbse_template_class_sections`** | academic structure | `id` | 2 | cbse_template, class_sections | Task Intelligence*, Scheduling | PUBLIC/NON-SENSITIVE METADATA | HIGH |
| **`cbse_template_term_exams`** | examination | `id` | 3 | cbse_exams, cbse_template, cbse_template_terms | Scheduling | INTERNAL BUSINESS DATA | HIGH |
| **`cbse_template_terms`** | configuration | `id` | 2 | cbse_template, cbse_terms | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`cbse_terms`** | academic structure | `id` | 0 | none | Task Intelligence* | INTERNAL BUSINESS DATA | HIGH |
| **`certificates`** | academic structure | `id` | 0 | none | Task Intelligence* | PERSONAL DATA | HIGH |
| **`chat_connections`** | communication | `id` | 2 | chat_users | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`chat_messages`** | communication | `id` | 2 | chat_connections, chat_users | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`chat_users`** | communication | `id` | 4 | staff, students | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`class_section_times`** | academic structure | `id` | 1 | class_sections | Task Intelligence* | INTERNAL BUSINESS DATA | HIGH |
| **`class_sections`** | academic structure | `id` | 2 | classes, sections | Task Intelligence*, Scheduling | PUBLIC/NON-SENSITIVE METADATA | HIGH |
| **`class_teacher`** | staff | `id` | 4 | classes, sections, sessions (+1) | Task Intelligence*, Scheduling | INTERNAL BUSINESS DATA | HIGH |
| **`classes`** | academic structure | `id` | 0 | none | Task Intelligence* | INTERNAL BUSINESS DATA | HIGH |
| **`complaint`** | communication | `id` | 0 | none | Workflow | PERSONAL DATA | HIGH |
| **`complaint_type`** | communication | `id` | 0 | none | Workflow | INTERNAL BUSINESS DATA | HIGH |
| **`content_for`** | academic structure | `id` | 2 | contents, users | Task Intelligence* | INTERNAL BUSINESS DATA | HIGH |
| **`content_types`** | academic structure | `id` | 0 | none | Task Intelligence* | INTERNAL BUSINESS DATA | HIGH |
| **`contents`** | academic structure | `id` | 3 | class_sections, classes, staff | Task Intelligence* | INTERNAL BUSINESS DATA | HIGH |
| **`course_category`** | academic structure | `id` | 0 | none | Task Intelligence* | INTERNAL BUSINESS DATA | HIGH |
| **`course_lesson_quiz_order`** | academic structure | `id` | 1 | online_course_section | Task Intelligence* | INTERNAL BUSINESS DATA | HIGH |
| **`course_progress`** | academic structure | `id` | 3 | online_course_section, online_courses, students | Task Intelligence* | INTERNAL BUSINESS DATA | HIGH |
| **`course_quiz_answer`** | academic structure | `id` | 3 | course_quiz_question, online_course_quiz, students | Task Intelligence* | INTERNAL BUSINESS DATA | HIGH |
| **`course_quiz_question`** | academic structure | `id` | 1 | online_course_quiz | Task Intelligence* | INTERNAL BUSINESS DATA | HIGH |
| **`course_rating`** | academic structure | `id` | 0 | none | Task Intelligence* | INTERNAL BUSINESS DATA | HIGH |
| **`cumulative_fine`** | finance | `id` | 0 | none | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`currencies`** | configuration | `id` | 0 | none | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`custom_field_values`** | configuration | `id` | 1 | custom_fields | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`custom_fields`** | configuration | `id` | 0 | none | None/Unknown | INTERNAL BUSINESS DATA | MEDIUM |
| **`daily_assignment`** | academic structure | `id` | 3 | staff, student_session, subject_group_subjects | Task Intelligence* | INTERNAL BUSINESS DATA | HIGH |
| **`department`** | staff | `id` | 0 | none | Task Intelligence*, Resource | INTERNAL BUSINESS DATA | HIGH |
| **`disable_reason`** | configuration | `id` | 0 | none | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`dispatch_receive`** | communication | `id` | 0 | none | None/Unknown | PERSONAL DATA | HIGH |
| **`email_attachments`** | communication | `id` | 1 | messages | Calendar | INTERNAL BUSINESS DATA | HIGH |
| **`email_config`** | communication | `id` | 0 | none | Calendar | HIGHLY SENSITIVE / RESTRICTED | HIGH |
| **`email_template`** | communication | `id` | 0 | none | Calendar | INTERNAL BUSINESS DATA | HIGH |
| **`email_template_attachment`** | communication | `id` | 0 | none | Calendar | PERSONAL DATA | HIGH |
| **`enquiry`** | communication | `id` | 3 | classes, staff | Workflow | PERSONAL DATA | HIGH |
| **`enquiry_type`** | communication | `id` | 0 | none | Workflow | INTERNAL BUSINESS DATA | HIGH |
| **`events`** | calendar/events | `id` | 1 | roles | Task Intelligence*, Calendar | INTERNAL BUSINESS DATA | HIGH |
| **`exam_group_class_batch_exam_students`** | students | `id` | 3 | exam_group_class_batch_exams, student_session, students | Scheduling | PERSONAL DATA | HIGH |
| **`exam_group_class_batch_exam_subjects`** | examination | `id` | 2 | exam_group_class_batch_exams, subjects | Scheduling | INTERNAL BUSINESS DATA | HIGH |
| **`exam_group_class_batch_exams`** | examination | `id` | 2 | exam_groups, sessions | Scheduling | PERSONAL DATA | HIGH |
| **`exam_group_exam_connections`** | examination | `id` | 2 | exam_group_class_batch_exams, exam_groups | Scheduling | INTERNAL BUSINESS DATA | HIGH |
| **`exam_group_exam_results`** | examination | `id` | 3 | exam_group_class_batch_exam_students, exam_group_class_batch_exam_subjects, exam_group_students | Scheduling, Reporting | INTERNAL BUSINESS DATA | HIGH |
| **`exam_group_students`** | students | `id` | 3 | exam_groups, student_session, students | Scheduling | INTERNAL BUSINESS DATA | HIGH |
| **`exam_groups`** | examination | `id` | 0 | none | Scheduling | INTERNAL BUSINESS DATA | HIGH |
| **`exam_schedules`** | examination | `id` | 2 | exams, sessions | Scheduling | INTERNAL BUSINESS DATA | HIGH |
| **`exams`** | examination | `id` | 1 | sessions | Scheduling | INTERNAL BUSINESS DATA | HIGH |
| **`expense_head`** | finance | `id` | 0 | none | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`expenses`** | finance | `id` | 1 | expense_head | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`fee_groups`** | finance | `id` | 0 | none | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`fee_groups_feetype`** | finance | `id` | 4 | fee_groups, fee_session_groups, feetype (+1) | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`fee_receipt_no`** | finance | `id` | 0 | none | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`fee_session_groups`** | academic structure | `id` | 2 | fee_groups, sessions | Task Intelligence* | PUBLIC/NON-SENSITIVE METADATA | HIGH |
| **`feemasters`** | finance | `id` | 3 | classes, feetype, sessions | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`fees_discounts`** | finance | `id` | 1 | sessions | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`fees_reminder`** | finance | `id` | 0 | none | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`feetype`** | finance | `id` | 0 | none | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`filetypes`** | configuration | `id` | 0 | none | None/Unknown | PERSONAL DATA | HIGH |
| **`follow_up`** | communication | `id` | 2 | enquiry, staff | Workflow | INTERNAL BUSINESS DATA | HIGH |
| **`front_cms_media_gallery`** | communication | `id` | 0 | none | None/Unknown | PERSONAL DATA | MEDIUM |
| **`front_cms_menu_items`** | communication | `id` | 1 | front_cms_menus | None/Unknown | PERSONAL DATA | HIGH |
| **`front_cms_menus`** | communication | `id` | 0 | none | None/Unknown | INTERNAL BUSINESS DATA | MEDIUM |
| **`front_cms_page_contents`** | communication | `id` | 1 | front_cms_pages | None/Unknown | PUBLIC/NON-SENSITIVE METADATA | HIGH |
| **`front_cms_pages`** | communication | `id` | 0 | none | None/Unknown | PERSONAL DATA | MEDIUM |
| **`front_cms_program_photos`** | communication | `id` | 1 | front_cms_programs | None/Unknown | PUBLIC/NON-SENSITIVE METADATA | HIGH |
| **`front_cms_programs`** | communication | `id` | 0 | none | None/Unknown | PERSONAL DATA | MEDIUM |
| **`front_cms_settings`** | communication | `id` | 0 | none | None/Unknown | PERSONAL DATA | MEDIUM |
| **`gateway_ins`** | integrations | `id` | 1 | online_admissions | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`gateway_ins_response`** | integrations | `id` | 1 | gateway_ins | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`general_calls`** | communication | `id` | 0 | none | None/Unknown | PERSONAL DATA | HIGH |
| **`gmeet`** | integrations | `id` | 3 | sessions, staff | Calendar | INTERNAL BUSINESS DATA | HIGH |
| **`gmeet_history`** | integrations | `id` | 3 | gmeet, staff, students | Calendar | INTERNAL BUSINESS DATA | HIGH |
| **`gmeet_sections`** | academic structure | `id` | 2 | class_sections, gmeet | Task Intelligence*, Calendar | PUBLIC/NON-SENSITIVE METADATA | HIGH |
| **`gmeet_settings`** | configuration | `id` | 0 | none | Calendar | HIGHLY SENSITIVE / RESTRICTED | MEDIUM |
| **`gmeet_staff`** | staff | `id` | 2 | gmeet, staff | Task Intelligence*, Resource, Calendar | INTERNAL BUSINESS DATA | HIGH |
| **`google_authenticator`** | authentication/security | `id` | 0 | none | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`grades`** | examination | `id` | 0 | none | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`guest`** | communication | `id` | 0 | none | None/Unknown | HIGHLY SENSITIVE / RESTRICTED | HIGH |
| **`holiday_type`** | calendar/events | `id` | 0 | none | Task Intelligence* | INTERNAL BUSINESS DATA | HIGH |
| **`homework`** | academic structure | `id` | 8 | classes, sections, sessions (+3) | Task Intelligence* | INTERNAL BUSINESS DATA | HIGH |
| **`homework_evaluation`** | academic structure | `id` | 3 | homework, student_session, students | Task Intelligence* | INTERNAL BUSINESS DATA | HIGH |
| **`hostel`** | hostel | `id` | 0 | none | None/Unknown | PERSONAL DATA | HIGH |
| **`hostel_rooms`** | hostel | `id` | 2 | hostel, room_types | Resource | INTERNAL BUSINESS DATA | HIGH |
| **`id_card`** | configuration | `id` | 0 | none | None/Unknown | PERSONAL DATA | HIGH |
| **`income`** | finance | `id` | 1 | income_head | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`income_head`** | finance | `id` | 0 | none | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`item`** | inventory | `id` | 3 | item_category, item_store, item_supplier | None/Unknown | PERSONAL DATA | HIGH |
| **`item_category`** | inventory | `id` | 0 | none | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`item_issue`** | inventory | `id` | 4 | item, item_category, staff | Workflow | INTERNAL BUSINESS DATA | HIGH |
| **`item_stock`** | inventory | `id` | 3 | item, item_store, item_supplier | Resource | INTERNAL BUSINESS DATA | HIGH |
| **`item_store`** | inventory | `id` | 0 | none | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`item_supplier`** | inventory | `id` | 0 | none | None/Unknown | PERSONAL DATA | HIGH |
| **`languages`** | configuration | `id` | 0 | none | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`leave_types`** | leave/availability | `id` | 0 | none | Workflow | PUBLIC/NON-SENSITIVE METADATA | HIGH |
| **`lesson`** | subjects/curriculum | `id` | 3 | sessions, subject_group_class_sections, subject_group_subjects | Task Intelligence* | INTERNAL BUSINESS DATA | HIGH |
| **`lesson_plan_forum`** | subjects/curriculum | `id` | 3 | staff, students, subject_syllabus | Task Intelligence* | INTERNAL BUSINESS DATA | HIGH |
| **`libarary_members`** | library | `id` | 0 | none | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`logs`** | reporting | `id` | 0 | none | Reporting | PERSONAL DATA | HIGH |
| **`mark_divisions`** | examination | `id` | 0 | none | Reporting | INTERNAL BUSINESS DATA | HIGH |
| **`messages`** | communication | `id` | 0 | none | None/Unknown | PERSONAL DATA | HIGH |
| **`migrations`** | configuration | `NONE` | 0 | none | None/Unknown | PUBLIC/NON-SENSITIVE METADATA | HIGH |
| **`notification_roles`** | roles/permissions | `id` | 2 | roles, send_notification | Calendar | PUBLIC/NON-SENSITIVE METADATA | HIGH |
| **`notification_setting`** | communication | `id` | 0 | none | Calendar | PERSONAL DATA | MEDIUM |
| **`offline_fees_payments`** | finance | `id` | 5 | fee_groups_feetype, staff, student_fees_master (+2) | None/Unknown | HIGHLY SENSITIVE / RESTRICTED | HIGH |
| **`online_admission_custom_field_value`** | students | `id` | 1 | custom_fields | Workflow | INTERNAL BUSINESS DATA | HIGH |
| **`online_admission_fields`** | students | `id` | 0 | none | Workflow | INTERNAL BUSINESS DATA | HIGH |
| **`online_admission_payment`** | students | `id` | 1 | online_admissions | Workflow | INTERNAL BUSINESS DATA | HIGH |
| **`online_admissions`** | students | `id` | 4 | categories, class_sections, hostel_rooms (+1) | Workflow | HIGHLY SENSITIVE / RESTRICTED | HIGH |
| **`online_course_class_sections`** | academic structure | `id` | 2 | class_sections, online_courses | Task Intelligence*, Scheduling | INTERNAL BUSINESS DATA | HIGH |
| **`online_course_lesson`** | academic structure | `id` | 1 | online_course_section | Task Intelligence* | INTERNAL BUSINESS DATA | HIGH |
| **`online_course_payment`** | academic structure | `id` | 0 | none | Task Intelligence* | INTERNAL BUSINESS DATA | HIGH |
| **`online_course_processing_payment`** | academic structure | `id` | 0 | none | Task Intelligence* | INTERNAL BUSINESS DATA | HIGH |
| **`online_course_quiz`** | academic structure | `id` | 2 | online_course_section, staff | Task Intelligence* | INTERNAL BUSINESS DATA | HIGH |
| **`online_course_section`** | academic structure | `id` | 1 | online_courses | Task Intelligence* | INTERNAL BUSINESS DATA | HIGH |
| **`online_course_settings`** | academic structure | `id` | 0 | none | Task Intelligence* | INTERNAL BUSINESS DATA | MEDIUM |
| **`online_courses`** | academic structure | `id` | 1 | staff | Task Intelligence* | INTERNAL BUSINESS DATA | HIGH |
| **`onlineexam`** | examination | `id` | 1 | sessions | Scheduling | INTERNAL BUSINESS DATA | HIGH |
| **`onlineexam_attempts`** | examination | `id` | 1 | onlineexam_students | Scheduling | INTERNAL BUSINESS DATA | HIGH |
| **`onlineexam_questions`** | examination | `id` | 3 | onlineexam, questions, sessions | Scheduling | INTERNAL BUSINESS DATA | HIGH |
| **`onlineexam_student_results`** | students | `id` | 2 | onlineexam_questions, onlineexam_students | Scheduling, Reporting | INTERNAL BUSINESS DATA | HIGH |
| **`onlineexam_students`** | students | `id` | 2 | onlineexam, student_session | Scheduling | INTERNAL BUSINESS DATA | HIGH |
| **`payment_settings`** | finance | `id` | 0 | none | None/Unknown | HIGHLY SENSITIVE / RESTRICTED | MEDIUM |
| **`payslip_allowance`** | payroll | `id` | 2 | staff, staff_payslip | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`permission_category`** | roles/permissions | `id` | 1 | permission_group | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`permission_group`** | roles/permissions | `id` | 0 | none | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`permission_student`** | students | `id` | 1 | permission_group | None/Unknown | PERSONAL DATA | HIGH |
| **`pickup_point`** | transport | `id` | 0 | none | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`print_headerfooter`** | configuration | `id` | 0 | none | None/Unknown | PERSONAL DATA | HIGH |
| **`qr_code_settings`** | configuration | `id` | 0 | none | None/Unknown | INTERNAL BUSINESS DATA | MEDIUM |
| **`questions`** | examination | `id` | 6 | class_sections, classes, sections (+2) | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`read_notification`** | communication | `id` | 3 | send_notification, staff, students | Calendar | PERSONAL DATA | HIGH |
| **`reference`** | communication | `id` | 0 | none | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`resume_additional_fields_settings`** | configuration | `id` | 0 | none | None/Unknown | INTERNAL BUSINESS DATA | MEDIUM |
| **`resume_settings_fields`** | configuration | `id` | 0 | none | None/Unknown | INTERNAL BUSINESS DATA | MEDIUM |
| **`roles`** | roles/permissions | `id` | 0 | none | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`roles_permissions`** | roles/permissions | `id` | 2 | permission_category, roles | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`room_types`** | hostel | `id` | 0 | none | Resource | INTERNAL BUSINESS DATA | HIGH |
| **`route_pickup_point`** | transport | `id` | 2 | pickup_point, transport_route | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`sch_settings`** | configuration | `id` | 0 | none | None/Unknown | HIGHLY SENSITIVE / RESTRICTED | MEDIUM |
| **`school_houses`** | academic structure | `id` | 0 | none | Task Intelligence* | INTERNAL BUSINESS DATA | HIGH |
| **`sections`** | academic structure | `id` | 0 | none | Task Intelligence* | INTERNAL BUSINESS DATA | HIGH |
| **`send_notification`** | communication | `id` | 1 | staff | Calendar | PERSONAL DATA | HIGH |
| **`sessions`** | academic structure | `id` | 0 | none | Task Intelligence* | INTERNAL BUSINESS DATA | HIGH |
| **`share_content_for`** | academic structure | `id` | 5 | class_sections, share_contents, staff (+2) | Task Intelligence* | PERSONAL DATA | HIGH |
| **`share_contents`** | academic structure | `id` | 1 | staff | Task Intelligence* | INTERNAL BUSINESS DATA | HIGH |
| **`share_upload_contents`** | academic structure | `id` | 2 | share_contents, upload_contents | Task Intelligence* | PUBLIC/NON-SENSITIVE METADATA | HIGH |
| **`sidebar_menus`** | configuration | `id` | 1 | permission_group | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`sidebar_sub_menus`** | configuration | `id` | 2 | permission_group, sidebar_menus | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`sms_config`** | communication | `id` | 0 | none | Calendar | HIGHLY SENSITIVE / RESTRICTED | HIGH |
| **`sms_template`** | communication | `id` | 0 | none | Calendar | INTERNAL BUSINESS DATA | HIGH |
| **`source`** | communication | `id` | 0 | none | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`staff`** | staff | `id` | 2 | department, staff_designation | Task Intelligence*, Resource | HIGHLY SENSITIVE / RESTRICTED | HIGH |
| **`staff_attendance`** | attendance | `id` | 2 | staff, staff_attendance_type | Resource, Reporting | HIGHLY SENSITIVE / RESTRICTED | HIGH |
| **`staff_attendance_type`** | attendance | `id` | 0 | none | Resource, Reporting | INTERNAL BUSINESS DATA | HIGH |
| **`staff_attendence_schedules`** | attendance | `id` | 0 | none | Scheduling, Resource, Reporting | INTERNAL BUSINESS DATA | HIGH |
| **`staff_designation`** | staff | `id` | 0 | none | Task Intelligence*, Resource | INTERNAL BUSINESS DATA | HIGH |
| **`staff_id_card`** | staff | `id` | 0 | none | Task Intelligence*, Resource | PERSONAL DATA | HIGH |
| **`staff_leave_details`** | leave/availability | `id` | 2 | leave_types, staff | Workflow, Resource | INTERNAL BUSINESS DATA | HIGH |
| **`staff_leave_request`** | leave/availability | `id` | 3 | leave_types, staff | Workflow, Resource | INTERNAL BUSINESS DATA | HIGH |
| **`staff_payroll`** | payroll | `id` | 0 | none | Resource | HIGHLY SENSITIVE / RESTRICTED | HIGH |
| **`staff_payslip`** | payroll | `id` | 1 | staff | Resource | HIGHLY SENSITIVE / RESTRICTED | HIGH |
| **`staff_rating`** | staff | `id` | 1 | staff | Task Intelligence*, Resource | INTERNAL BUSINESS DATA | HIGH |
| **`staff_roles`** | staff | `id` | 2 | roles, staff | Task Intelligence*, Resource | INTERNAL BUSINESS DATA | HIGH |
| **`staff_timeline`** | staff | `id` | 1 | staff | Task Intelligence*, Resource | PERSONAL DATA | HIGH |
| **`student_applied_discounts`** | students | `id` | 2 | student_fees_deposite, student_fees_discounts | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`student_applyleave`** | students | `id` | 2 | staff, student_session | Workflow | INTERNAL BUSINESS DATA | HIGH |
| **`student_attendence_schedules`** | attendance | `id` | 0 | none | Scheduling, Reporting | INTERNAL BUSINESS DATA | HIGH |
| **`student_attendences`** | attendance | `id` | 2 | attendence_type, student_session | Reporting | HIGHLY SENSITIVE / RESTRICTED | HIGH |
| **`student_behaviour`** | students | `id` | 0 | none | None/Unknown | PERSONAL DATA | HIGH |
| **`student_dashboard_settings`** | students | `id` | 0 | none | None/Unknown | PERSONAL DATA | MEDIUM |
| **`student_doc`** | students | `id` | 0 | none | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`student_edit_fields`** | students | `id` | 0 | none | None/Unknown | PERSONAL DATA | HIGH |
| **`student_educational_details`** | students | `id` | 0 | none | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`student_fees`** | finance | `id` | 2 | feemasters, student_session | None/Unknown | PERSONAL DATA | HIGH |
| **`student_fees_deposite`** | finance | `id` | 3 | fee_groups_feetype, student_fees_master, student_transport_fees | Reporting | INTERNAL BUSINESS DATA | HIGH |
| **`student_fees_discounts`** | finance | `id` | 2 | fees_discounts, student_session | None/Unknown | PERSONAL DATA | HIGH |
| **`student_fees_master`** | finance | `id` | 2 | fee_session_groups, student_session | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`student_fees_processing`** | finance | `id` | 4 | fee_groups_feetype, gateway_ins, student_fees_master (+1) | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`student_incident_comments`** | students | `id` | 1 | student_incidents | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`student_incidents`** | students | `id` | 2 | student_behaviour, students | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`student_quiz_status`** | academic structure | `id` | 2 | online_course_quiz, students | Task Intelligence* | INTERNAL BUSINESS DATA | HIGH |
| **`student_refrence`** | students | `id` | 0 | none | None/Unknown | PERSONAL DATA | HIGH |
| **`student_session`** | students | `id` | 7 | classes, hostel_rooms, route_pickup_point (+4) | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`student_skills_detail`** | students | `id` | 0 | none | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`student_subject_attendances`** | attendance | `id` | 3 | attendence_type, student_session, subject_timetable | Reporting | INTERNAL BUSINESS DATA | HIGH |
| **`student_timeline`** | students | `id` | 1 | students | None/Unknown | PERSONAL DATA | HIGH |
| **`student_transport_fees`** | finance | `id` | 3 | route_pickup_point, student_session, transport_feemaster | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`student_work_experience`** | students | `id` | 0 | none | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`students`** | students | `id` | 0 | none | None/Unknown | HIGHLY SENSITIVE / RESTRICTED | HIGH |
| **`subject_group_class_sections`** | academic structure | `id` | 3 | class_sections, sessions, subject_groups | Task Intelligence*, Scheduling | INTERNAL BUSINESS DATA | HIGH |
| **`subject_group_subjects`** | subjects/curriculum | `id` | 3 | sessions, subject_groups, subjects | Task Intelligence* | PUBLIC/NON-SENSITIVE METADATA | HIGH |
| **`subject_groups`** | subjects/curriculum | `id` | 1 | sessions | Task Intelligence* | INTERNAL BUSINESS DATA | HIGH |
| **`subject_syllabus`** | subjects/curriculum | `id` | 4 | sessions, staff, topic | Task Intelligence* | INTERNAL BUSINESS DATA | HIGH |
| **`subject_timetable`** | subjects/curriculum | `id` | 6 | classes, sections, sessions (+3) | Task Intelligence*, Scheduling | INTERNAL BUSINESS DATA | HIGH |
| **`subjects`** | subjects/curriculum | `id` | 0 | none | Task Intelligence* | INTERNAL BUSINESS DATA | HIGH |
| **`submit_assignment`** | academic structure | `id` | 2 | homework, students | Task Intelligence* | INTERNAL BUSINESS DATA | HIGH |
| **`template_admitcards`** | configuration | `id` | 0 | none | None/Unknown | PERSONAL DATA | HIGH |
| **`template_marksheets`** | examination | `id` | 0 | none | Reporting | PERSONAL DATA | HIGH |
| **`thermal_print_settings`** | configuration | `id` | 0 | none | None/Unknown | PERSONAL DATA | MEDIUM |
| **`topic`** | subjects/curriculum | `id` | 2 | lesson, sessions | Task Intelligence* | INTERNAL BUSINESS DATA | HIGH |
| **`transport_feemaster`** | finance | `id` | 1 | sessions | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`transport_route`** | transport | `id` | 0 | none | None/Unknown | INTERNAL BUSINESS DATA | HIGH |
| **`upload_contents`** | academic structure | `id` | 2 | content_types, staff | Task Intelligence* | PERSONAL DATA | HIGH |
| **`user_google_authenticate_codes`** | authentication/security | `id` | 2 | staff, users | None/Unknown | HIGHLY SENSITIVE / RESTRICTED | HIGH |
| **`userlog`** | authentication/security | `id` | 1 | class_sections | Reporting | PERSONAL DATA | HIGH |
| **`users`** | authentication/security | `id` | 0 | none | None/Unknown | HIGHLY SENSITIVE / RESTRICTED | HIGH |
| **`users_authentication`** | authentication/security | `id` | 0 | none | None/Unknown | HIGHLY SENSITIVE / RESTRICTED | HIGH |
| **`vehicle_routes`** | transport | `id` | 2 | transport_route, vehicles | Resource | PUBLIC/NON-SENSITIVE METADATA | HIGH |
| **`vehicles`** | transport | `id` | 0 | none | Resource | PERSONAL DATA | HIGH |
| **`video_tutorial`** | academic structure | `id` | 1 | staff | Task Intelligence* | INTERNAL BUSINESS DATA | HIGH |
| **`video_tutorial_class_sections`** | academic structure | `id` | 2 | class_sections, video_tutorial | Task Intelligence*, Scheduling | INTERNAL BUSINESS DATA | HIGH |
| **`visitors_book`** | library | `id` | 2 | staff, student_session | Workflow | PERSONAL DATA | HIGH |
| **`visitors_purpose`** | communication | `id` | 0 | none | Workflow | PERSONAL DATA | HIGH |

*\* Marked as candidate only: requires formal Task 0G.2 mapping review before ingestion.*

---

## 6. Relationship Overview
The 307 foreign key relationships organize the MySQL schema into several major structural clusters:

### 6.1 Top Hub Tables by Inbound References
These tables represent the central master entities upon which the rest of the operational schema depends:
| Hub Table | Inbound FK Count | Unique Referencing Tables | Domain Role |
| :--- | :---: | :---: | :--- |
| **`staff`** | 43 | 35 | Master entity in institutional operations |
| **`sessions`** | 26 | 26 | Master entity in institutional operations |
| **`student_session`** | 19 | 19 | Master entity in institutional operations |
| **`students`** | 17 | 16 | Master entity in institutional operations |
| **`class_sections`** | 12 | 12 | Master entity in institutional operations |
| **`classes`** | 10 | 10 | Master entity in institutional operations |
| **`cbse_exams`** | 7 | 6 | Master entity in institutional operations |
| **`subjects`** | 7 | 6 | Master entity in institutional operations |
| **`sections`** | 6 | 6 | Master entity in institutional operations |
| **`cbse_template`** | 5 | 5 | Master entity in institutional operations |
| **`online_course_section`** | 4 | 4 | Master entity in institutional operations |
| **`subject_group_subjects`** | 4 | 4 | Master entity in institutional operations |
| **`roles`** | 4 | 4 | Master entity in institutional operations |
| **`permission_group`** | 4 | 4 | Master entity in institutional operations |
| **`cbse_terms`** | 3 | 3 | Master entity in institutional operations |

### 6.2 Structural Clusters
1. **Core Academic Spine**: Anchored by `sessions` (26 inbound FKs), `classes` (10 inbound FKs), `sections` (6 inbound FKs), and `class_sections` (12 inbound FKs). Connects students (`student_session`), academic courses, and instructional assignments.
2. **Staff & Human Resource Cluster**: Anchored by `staff` (43 inbound FKs across 35 unique tables), linking `department`, `staff_designation`, `roles`, `staff_leave_request`, `staff_attendance`, and operational activities.
3. **Student Information & Enrolment Cluster**: Anchored by `students` (17 inbound FKs) and `student_session` (19 inbound FKs), capturing student lifecycle, admissions, guardians, fee allocations, and attendance.
4. **Examination & CBSE Assessment Subsystem**: Centered on `cbse_exams` (7 inbound FKs), `cbse_exam_timetable`, `cbse_exam_assessments`, `cbse_student_subject_marks`, and grade ranges.
5. **Timetable & Subject Scheduling Cluster**: Links `subjects` (7 inbound FKs), `subject_group`, `subject_group_subjects`, and `subject_timetable` (which binds staff, class sections, sessions, and time slots).
6. **Financial & Fee Management Network**: Formed by `fee_groups`, `fee_masters`, `student_fees_master`, `student_fees_deposite`, and `offline_fees_payments`.
7. **Completely Isolated Subsystems (66 Tables)**: 66 tables have 0 inbound and 0 outbound foreign keys. These include cloud settings (`aws_s3_settings`), configuration flags (`behaviour_settings`, `sch_settings`), visitor books, CMS pages (`front_cms_*`), and standalone lookup scales.

---

## 7. Preliminary E-7 Domain Relevance

### 7.1 Task Intelligence Candidates
> [!NOTE]
> **CANDIDATE ONLY — REQUIRES TASK 0G.2 MAPPING REVIEW**  
> These tables represent potential operational context that the Task Intelligence Worker may draw upon (via sanitized read-only adapters, never raw table dumps) for prompt construction, work breakdown structures, and institutional awareness.
* **Total Candidate Tables**: `61`
* **Identified Candidate Tables**: `annual_calendar`, `cbse_exams`, `cbse_template_class_sections`, `cbse_terms`, `certificates`, `class_section_times`, `class_sections`, `class_teacher`, `classes`, `content_for`, `content_types`, `contents`, `course_category`, `course_lesson_quiz_order`, `course_progress`, `course_quiz_answer`, `course_quiz_question`, `course_rating`, `daily_assignment`, `department`, `events`, `fee_session_groups`, `gmeet_sections`, `gmeet_staff`, `holiday_type`, `homework`, `homework_evaluation`, `lesson`, `lesson_plan_forum`, `online_course_class_sections`, `online_course_lesson`, `online_course_payment`, `online_course_processing_payment`, `online_course_quiz`, `online_course_section`, `online_course_settings`, `online_courses`, `school_houses`, `sections`, `sessions`, `share_content_for`, `share_contents`, `share_upload_contents`, `staff`, `staff_designation`, `staff_id_card`, `staff_rating`, `staff_roles`, `staff_timeline`, `student_quiz_status`, `subject_group_class_sections`, `subject_group_subjects`, `subject_groups`, `subject_syllabus`, `subject_timetable`, `subjects`, `submit_assignment`, `topic`, `upload_contents`, `video_tutorial`, `video_tutorial_class_sections`
* **Context Value Provided**: Institutional academic hierarchies (`classes`, `sections`, `class_sections`), organizational structures (`department`, `staff_designation`), staff capabilities (`staff`), curricula (`subjects`, `subject_group`, `lesson`, `topic`), and institutional calendars (`annual_calendar`, `events`, `sessions`).

### 7.2 Workflow Engine Candidates
* **Total Candidate Tables**: `17`
* **Identified Candidate Tables**: `book_issues`, `complaint`, `complaint_type`, `enquiry`, `enquiry_type`, `follow_up`, `item_issue`, `leave_types`, `online_admission_custom_field_value`, `online_admission_fields`, `online_admission_payment`, `online_admissions`, `staff_leave_details`, `staff_leave_request`, `student_applyleave`, `visitors_book`, `visitors_purpose`
* **Workflow Semantics**: Multi-stage approval lifecycles, human decision tasks, and request states represented in MySQL (`apply_leave`, `staff_leave_request`, `student_apply_leave`, `complaint`, `enquiry`, `item_issue`, `online_admissions`).

### 7.3 Scheduling Engine Candidates
* **Total Candidate Tables**: `36`
* **Identified Candidate Tables**: `cbse_exam_assessment_types`, `cbse_exam_assessments`, `cbse_exam_class_sections`, `cbse_exam_grades`, `cbse_exam_grades_range`, `cbse_exam_observations`, `cbse_exam_student_subject_rank`, `cbse_exam_students`, `cbse_exam_timetable`, `cbse_exam_timetable_assessment_types`, `cbse_exams`, `cbse_student_exam_ranks`, `cbse_template_class_sections`, `cbse_template_term_exams`, `class_sections`, `class_teacher`, `exam_group_class_batch_exam_students`, `exam_group_class_batch_exam_subjects`, `exam_group_class_batch_exams`, `exam_group_exam_connections`, `exam_group_exam_results`, `exam_group_students`, `exam_groups`, `exam_schedules`, `exams`, `online_course_class_sections`, `onlineexam`, `onlineexam_attempts`, `onlineexam_questions`, `onlineexam_student_results`, `onlineexam_students`, `staff_attendence_schedules`, `student_attendence_schedules`, `subject_group_class_sections`, `subject_timetable`, `video_tutorial_class_sections`
* **Scheduling Semantics**: Academic timetables (`subject_timetable`, `cbse_exam_timetable`, `exam_schedules`), section assignments (`class_sections`, `class_teacher`), and shift schedules (`staff_attendance_type`).

### 7.4 Resource Management Candidates
* **Total Candidate Tables**: `20`
* **Identified Candidate Tables**: `department`, `gmeet_staff`, `hostel_rooms`, `item_stock`, `room_types`, `staff`, `staff_attendance`, `staff_attendance_type`, `staff_attendence_schedules`, `staff_designation`, `staff_id_card`, `staff_leave_details`, `staff_leave_request`, `staff_payroll`, `staff_payslip`, `staff_rating`, `staff_roles`, `staff_timeline`, `vehicle_routes`, `vehicles`
* **Resource Semantics**: Human personnel (`staff`, `roles`), physical vehicles (`vehicles`), hostel spaces (`hostel_rooms`), and consumable inventory items (`item_stock`, `items`, `item_store`).

### 7.5 Calendar & Integration Candidates
* **Total Candidate Tables**: `19`
* **Identified Candidate Tables**: `alumni_events`, `annual_calendar`, `aws_s3_settings`, `email_attachments`, `email_config`, `email_template`, `email_template_attachment`, `events`, `gmeet`, `gmeet_history`, `gmeet_sections`, `gmeet_settings`, `gmeet_staff`, `notification_roles`, `notification_setting`, `read_notification`, `send_notification`, `sms_config`, `sms_template`
* **Integration Semantics**: Institutional dates and holidays (`annual_calendar`, `events`, `holiday_type`), virtual conferencing meetings (`gmeet`, `gmeet_staff`), email/SMS notifications (`email_config`, `messages`, `sms_config`), and cloud storage (`aws_s3_settings`).

### 7.6 Currently Not Required / Unknown Tables
* **Total Tables**: `105`
* **Examples**: Standalone auxiliary and website/portal modules: `addon_versions`, `addons`, `alumni_students`, `behaviour_settings`, `books`, `captcha`, `categories`, `cbse_observation_class_section`, `cbse_observation_parameters`, `cbse_observation_subparameter`, `cbse_observation_term_student_subparameter`, `cbse_observation_terms`, `cbse_template`, `cbse_template_terms`, `chat_connections`, `chat_messages`, `chat_users`, `cumulative_fine`, `currencies`, `custom_field_values`, etc.

---

## 8. Sensitive Data Surface
Schema-level inspection identified **49 highly sensitive columns** and **219 personal data columns** across the database. No concrete row values were extracted or copied.

### 8.1 API Credentials & Cryptographic Secrets (`14` columns)
* **Columns Detected**: `aws_s3_settings.api_key`, `aws_s3_settings.api_secret`, `email_config.api_key`, `email_config.api_secret`, `gmeet_settings.api_key`, `gmeet_settings.api_secret`, `guest.verification_code`, `payment_settings.api_secret_key`, `payment_settings.salt`, `sch_settings.cron_secret_key`, `staff.verification_code`, `user_google_authenticate_codes.secret_code`, `users.verification_code`, `users_authentication.token`

### 8.2 Authentication & Password Hashes (`6` columns)
* **Columns Detected**: `email_config.smtp_password`, `guest.password`, `payment_settings.api_password`, `sms_config.password`, `staff.password`, `users.password`

### 8.3 Banking & Account Information (`15` columns)
* **Columns Detected**: `offline_fees_payments.bank_account_transferred`, `online_admissions.bank_account_no`, `online_admissions.bank_name`, `online_admissions.ifsc_code`, `payment_settings.account_no`, `sch_settings.bank_account_no`, `sch_settings.bank_name`, `sch_settings.ifsc_code`, `staff.bank_account_no`, `staff.bank_branch`, `staff.bank_name`, `staff.ifsc_code`, `students.bank_account_no`, `students.bank_name`, `students.ifsc_code`

### 8.4 Biometric Identification Metadata (`6` columns)
* **Columns Detected**: `sch_settings.biometric`, `sch_settings.biometric_device`, `staff_attendance.biometric_attendence`, `staff_attendance.biometric_device_data`, `student_attendences.biometric_attendence`, `student_attendences.biometric_device_data`

### 8.5 Dates of Birth (`9` columns)
* **Columns Detected**: `cbse_template.is_dob`, `guest.dob`, `id_card.enable_dob`, `online_admissions.dob`, `staff.dob`, `staff_id_card.enable_staff_dob`, `students.dob`, `template_admitcards.is_dob`, `template_marksheets.is_dob`

### 8.6 Demographic & Community Attributes (`12` columns)
* **Columns Detected**: `guest.gender`, `id_card.enable_blood_group`, `online_admissions.blood_group`, `online_admissions.gender`, `online_admissions.religion`, `sch_settings.is_blood_group`, `sch_settings.religion`, `staff.gender`, `students.blood_group`, `students.gender`, `students.religion`, `template_admitcards.is_gender`

### 8.7 Family & Guardian Information (`44` columns)
* **Columns Detected**: `cbse_template.is_father_name`, `cbse_template.is_mother_name`, `front_cms_menu_items.parent_id`, `notification_setting.display_guardian_recipient`, `notification_setting.is_guardian_recipient`, `online_admissions.father_name`, `online_admissions.guardian_is`, `online_admissions.guardian_name`, `online_admissions.guardian_occupation`, `online_admissions.guardian_pic`, `online_admissions.guardian_relation`, `online_admissions.mother_name`, `permission_student.parent`, `read_notification.parent_id`, `sch_settings.father_name`, `sch_settings.guardian_delete_chat`, `sch_settings.guardian_name`, `sch_settings.guardian_occupation`, `sch_settings.guardian_pic`, `sch_settings.guardian_relation`, `sch_settings.mother_name`, `sch_settings.parent_login`, `sch_settings.parent_panel_login`, `sch_settings.staff_father_name`, `sch_settings.staff_mother_name`, `send_notification.visible_parent`, `share_content_for.user_parent_id`, `staff.father_name`, `staff.mother_name`, `student_dashboard_settings.is_parent`, `students.father_name`, `students.guardian_is`, `students.guardian_name`, `students.guardian_occupation`, `students.guardian_pic`, `students.guardian_relation`, `students.mother_name`, `students.parent_app_key`, `students.parent_id`, `template_admitcards.is_father_name`, `template_admitcards.is_mother_name`, `template_marksheets.is_father_name`, `template_marksheets.is_mother_name`, `users.childs`

### 8.8 Government Identification Numbers (`1` columns)
* **Columns Detected**: `sch_settings.national_identification_no`

### 8.9 Other Personal Identifiers (`48` columns)
* **Columns Detected**: `alumni_events.note`, `cbse_exam_students.roll_no`, `cbse_exams.use_exam_roll_no`, `cbse_student_subject_marks.note`, `cbse_student_subject_result.note`, `cbse_template.is_admission_no`, `cbse_template.is_roll_no`, `exam_group_class_batch_exam_students.roll_no`, `exam_group_class_batch_exams.use_exam_roll_no`, `front_cms_settings.instagram_url`, `front_cms_settings.linkedin_url`, `front_cms_settings.twitter_url`, `guest.note`, `id_card.enable_admission_no`, `online_admissions.admission_no`, `online_admissions.firstname`, `online_admissions.lastname`, `online_admissions.roll_no`, `permission_student.name`, `sch_settings.lastname`, `sch_settings.roll_no`, `staff.facebook`, `staff.instagram`, `staff.linkedin`, `staff.name`, `staff.note`, `staff.surname`, `staff.twitter`, `staff_timeline.description`, `student_behaviour.description`, `student_dashboard_settings.name`, `student_edit_fields.name`, `student_fees.description`, `student_fees_discounts.description`, `student_refrence.name`, `student_timeline.description`, `students.admission_no`, `students.firstname`, `students.lastname`, `students.note`, `students.roll_no`, `template_admitcards.is_admission_no`, `template_admitcards.is_roll_no`, `template_marksheets.is_admission_no`, `template_marksheets.is_roll_no`, `visitors_book.name`, `visitors_book.note`, `visitors_purpose.description`

### 8.10 Payroll & Compensation Records (`7` columns)
* **Columns Detected**: `sch_settings.staff_basic_salary`, `sch_settings.staff_epf_no`, `staff.basic_salary`, `staff.epf_no`, `staff.payscale`, `staff_payroll.basic_salary`, `staff_payslip.net_salary`

### 8.11 Personal Documents & Biometric Images (`37` columns)
* **Columns Detected**: `addons.image`, `alumni_events.photo`, `alumni_students.photo`, `cbse_template.header_image`, `cbse_template.is_photo`, `certificates.background_image`, `certificates.enable_image_height`, `certificates.enable_student_image`, `complaint.image`, `dispatch_receive.image`, `filetypes.image_extension`, `filetypes.image_mime`, `filetypes.image_size`, `front_cms_media_gallery.image`, `front_cms_pages.feature_image`, `front_cms_programs.feature_image`, `guest.guest_image`, `id_card.sign_image`, `item.item_photo`, `online_admissions.image`, `print_headerfooter.header_image`, `sch_settings.image`, `sch_settings.staff_photo`, `sch_settings.student_photo`, `sch_settings.student_resume_download`, `staff.image`, `staff.joining_letter`, `staff.resignation_letter`, `staff.resume`, `staff_id_card.sign_image`, `students.image`, `template_admitcards.is_photo`, `template_marksheets.header_image`, `template_marksheets.is_photo`, `upload_contents.image`, `vehicles.vehicle_photo`, `visitors_book.image`

### 8.12 Personal Email Addresses (`21` columns)
* **Columns Detected**: `alumni_students.current_email`, `complaint.email`, `email_config.email_type`, `email_config.smtp_email`, `email_template_attachment.email_template_id`, `enquiry.email`, `front_cms_settings.complain_form_email`, `guest.email`, `item_supplier.email`, `messages.email_template_id`, `online_admissions.email`, `online_admissions.guardian_email`, `payment_settings.api_email`, `sch_settings.email`, `sch_settings.guardian_email`, `sch_settings.staff_notification_email`, `sch_settings.student_email`, `staff.email`, `students.email`, `students.guardian_email`, `visitors_book.email`

### 8.13 Personal Phone / Mobile Numbers (`38` columns)
* **Columns Detected**: `alumni_students.current_phone`, `complaint.contact`, `enquiry.contact`, `front_cms_settings.contact_us_email`, `general_calls.contact`, `guest.mobileno`, `id_card.enable_phone`, `item_supplier.contact_person_email`, `item_supplier.contact_person_name`, `item_supplier.contact_person_phone`, `item_supplier.phone`, `online_admissions.father_phone`, `online_admissions.guardian_phone`, `online_admissions.mobileno`, `online_admissions.mother_phone`, `sch_settings.admin_mobile_api_url`, `sch_settings.admin_panel_whatsapp_mobile`, `sch_settings.father_phone`, `sch_settings.front_side_whatsapp_mobile`, `sch_settings.guardian_phone`, `sch_settings.mobile_api_url`, `sch_settings.mobile_no`, `sch_settings.mother_phone`, `sch_settings.phone`, `sch_settings.staff_emergency_contact`, `sch_settings.staff_phone`, `sch_settings.student_panel_whatsapp_mobile`, `sms_config.contact`, `staff.contact_no`, `staff.emergency_contact_no`, `staff_id_card.enable_staff_phone`, `student_refrence.contact`, `students.father_phone`, `students.guardian_phone`, `students.mobileno`, `students.mother_phone`, `vehicles.driver_contact`, `visitors_book.contact`

### 8.14 Residential Addresses (`28` columns)
* **Columns Detected**: `alumni_students.address`, `dispatch_receive.address`, `enquiry.address`, `guest.address`, `hostel.address`, `id_card.enable_address`, `id_card.school_address`, `item_supplier.address`, `logs.ip_address`, `online_admissions.current_address`, `online_admissions.guardian_address`, `online_admissions.permanent_address`, `sch_settings.address`, `sch_settings.current_address`, `sch_settings.guardian_address`, `sch_settings.permanent_address`, `sch_settings.staff_current_address`, `sch_settings.staff_permanent_address`, `staff.local_address`, `staff.permanent_address`, `staff_id_card.enable_permanent_address`, `staff_id_card.school_address`, `students.current_address`, `students.guardian_address`, `students.permanent_address`, `template_admitcards.is_address`, `thermal_print_settings.address`, `userlog.ipaddress`

> [!WARNING]
> **SECURITY & INGESTION FILTERING MANDATE**  
> All columns flagged as `HIGHLY SENSITIVE / RESTRICTED` (especially password hashes, tokens, salts, banking accounts, and payroll records) must be strictly excluded at the MySQL ingestion adapter boundary. Under zero circumstances may raw authentication credentials or banking attributes be ingested into E-7 domain memory or LLM prompt contexts.

---

## 9. Physical Resource / Room Model Findings
A dedicated architectural inspection of physical facility and equipment modeling in the company MySQL database yielded the following critical findings:

### 9.1 Absence of a Canonical `rooms` Table
* **Finding**: There is **no canonical `rooms` table** in the company MySQL database.
* **Schema Evidence**: Academic room assignments exist solely as unconstrained string columns:
  - `subject_timetable.room_no` (`varchar(255)`)
  - `cbse_exam_timetable.room_no` (`varchar(255)`)
  - `exam_schedules.room_no` (`varchar(50)`)
  - `exam_group_class_batch_exam_subjects.room_no` (`varchar(255)`)
* **Hostel Exception**: The table `hostel_rooms` exists, but it models residential dormitory beds (`room_no`, `no_of_bed`, `cost_per_bed`, `room_type_id`), not educational classrooms, auditoriums, laboratories, or exam halls.

### 9.2 Complete Absence of Room Capacity Metadata
* **Finding**: Educational room capacity is **completely absent** from the MySQL schema.
* **Schema Evidence**: No column across any academic, classroom, or timetable table defines seating capacity, exam desk spacing, or maximum occupancy.

### 9.3 Complete Absence of Equipment & Capability Modeling
* **Finding**: Equipment, machinery, technical capabilities, and physical resource availability calendars do **not exist** in the MySQL schema.
* **Schema Evidence**: The inventory subsystem (`items`, `item_stock`, `item_store`, `item_issue`) models warehouse consumables and supply receipts, not bookable physical equipment (e.g. projectors, lab apparatus, HVAC machinery, network test kits) or multi-resource temporal availability.

### 9.4 Complete Absence of Invigilator Role Modeling
* **Finding**: Examination invigilation roles and invigilator assignments are **completely absent**.
* **Schema Evidence**: The examination subsystem tracks exams, assessment parameters, schedules, student marks, and ranks, but has zero fields or constraints linking staff members as invigilators to exam rooms or exam timetable slots.

### 9.5 Architectural Conclusion on Physical Resources
In accordance with the **No-Hallucination Rule**, E-7 must not attempt to invent room capacity, equipment capabilities, or invigilation constraints from unconstrained strings like `room_no`. The architectural position regarding physical resource authority is defined as follows:

1. **Schema Fact**: The analyzed company MySQL schema does not provide a sufficient canonical academic physical-resource model for scheduling requirements such as:
   - academic rooms
   - room capacity
   - room type
   - room equipment/capabilities
   - equipment availability
   - explicit exam invigilator assignment constraints
2. **Architectural Conclusion**: The authoritative source for this missing physical-resource metadata is currently **UNRESOLVED** and requires business/integration clarification.
3. **Mode-Specific Rule**:
   - In *E-7 Universal Mode*, the E-7 `resource-service` **MAY** become authoritative for maintaining academic room inventories, room capacities, equipment profiles, and physical resource availability.
   - In *Embedded Mode*, another external ERP, SIS, facility management, or campus resource system **MAY** remain authoritative, and E-7 should consume and mirror that metadata through an adapter.
4. **MySQL Reference Labels**: Existing MySQL `room_no` string fields (e.g. in `subject_timetable`, `cbse_exam_timetable`, `exam_schedules`) are references or display labels requiring normalization or mapping. They must not be treated as a complete resource master.
5. **Fixture Governance & Authority Separation**: The test fixture [`tests/fixtures/resources/RS-SYNTH-001.json`](file:///F:/operations-engine/tests/fixtures/resources/RS-SYNTH-001.json) is **synthetic Phase-0 test data only**. It **MUST NOT** be described as a production source of authority or as evidence that E-7 owns production room/resource master data.
6. **Preservation of E-7 State Ownership**: PostgreSQL remains canonical for **E-7-OWNED operational state** such as goals, plans, tasks, task dependencies, milestones, task risks, workflow mappings, E-7 SLA policy mappings, constraints, schedule jobs, schedule assignments, provider mappings, domain events (outbox/inbox), and audit events. This decision is strictly preserved.

---

## 10. External-ID Considerations
* **Integer Primary Keys**: Almost all MySQL tables utilize MySQL auto-increment `int(11)` primary keys (e.g. `staff.id`, `classes.id`, `students.id`).
* **Strict Identifier Non-Pollution**: As codified in [`docs/data-model/CANONICAL_OWNERSHIP_AND_SERVICE_BOUNDARIES.md`](file:///F:/operations-engine/docs/data-model/CANONICAL_OWNERSHIP_AND_SERVICE_BOUNDARIES.md), external MySQL integer IDs **MUST NOT** silently become canonical E-7 primary keys.
* **Adapter Mapping Registry**: E-7 will maintain explicit mapping tables (e.g., `external_system_mappings` linking `(tenant_id, system_name='iredu_mysql', entity_type, external_id)` $\leftrightarrow$ `canonical_uuid`) in PostgreSQL.

---

## 11. Architecture Impact
Analysis of the company MySQL schema produces the following definitive architectural conclusions:

1. **PostgreSQL as Canonical System of Record**: **NO CHANGE**. PostgreSQL remains the single authoritative System of Record for E-7 operational state. The company MySQL database is treated strictly as an external ERP upstream source.
2. **Adapter Boundary Architecture**: **NO CHANGE**. An IR-owned MySQL read-only ingestion adapter will encapsulate all SQL querying, connection pools, and dialect translation behind canonical domain interfaces.
3. **Separation of AI Reasoning**: **NO CHANGE**. LLMs will never receive raw database connections or direct access to MySQL schema tables.
4. **Phase Boundaries**: **NO CHANGE**. The project remains strictly in **Phase 0**. Zero Phase-1 application code or migrations have been triggered.

---

## 12. Data Gaps / Business Clarifications Required
Prior to implementation in later phases, the following business clarifications are recorded for review:
1. **Physical Resource & Room Master Authority**: Clarify the authoritative source for academic room master data, room capacities, and equipment definitions (whether mastered by E-7 `resource-service` in Universal Mode or consumed/mirrored from an external facility management/SIS system in Embedded Mode), given that the analyzed MySQL schema only provides unconstrained `room_no` reference strings.
2. **Exam Invigilation Assignment**: Clarify whether exam invigilation is currently managed offline or in a separate module not captured in the `iredu` export.
3. **Multi-Tenancy in MySQL**: Confirm whether the MySQL database is single-tenant per school instance or multi-tenant (and if so, what column governs institutional partitioning, e.g. `sch_settings` or database-per-institution).
4. **Soft Deletes vs Hard Deletes**: Many tables feature `is_active` (`yes`/`no` or `1`/`0`) while others lack any status flags. Ingestion sync policies must clarify tombstone semantics.
5. **CBSE vs Non-CBSE Examination Redundancy**: The schema contains dual examination structures (`cbse_exams` vs `exam_groups` / `exam_schedules`). Business clarification is required on which model is active in target client institutions.

---

## 13. Recommendations for Task 0G.2
When authorized to proceed to **Task 0G.2 (Detailed Entity-to-Entity Mapping Review)**, the team should:
1. Formulate formal mapping specifications connecting candidate MySQL tables (`staff`, `classes`, `sections`, `subjects`, `subject_timetable`, `cbse_exams`) to E-7 canonical schemas.
2. Define the schema contract for the external mapping repository in PostgreSQL.
3. Specify the data sanitization and masking pipeline for sensitive staff and student attributes prior to staging.
4. Maintain all existing Phase-0 governance invariants and wait for formal approval before writing adapter code in Phase 1.
