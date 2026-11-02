# MOCK Product Requirements Document: CSV Batch User Import

> **MOCK / NON-AUTHORITATIVE SAMPLE**
>
> This document describes a fictional feature for evaluation and testing purposes only. It is intended to test a PRD-generation system built with Strands and Amazon Bedrock AgentCore. It does not describe an actual product, repository, API, customer commitment, implementation plan, AWS account, or deployed resource.

## 1. Document Control

> **MOCK / NON-AUTHORITATIVE SAMPLE**
>
> This document describes a fictional feature for evaluation and testing purposes only. It is intended to test a PRD-generation system built with Strands and Amazon Bedrock AgentCore. It does not describe an actual product, repository, API, customer commitment, implementation plan, AWS account, or deployed resource.

| Field | Value |
|---|---|
| Title | CSV Batch User Import — Validation Preview Enhancement |
| Version | mock-csv-import-v0.1 |
| Status | Draft |
| Language | en-US |
| Source SHA-256 | c27d1a2277bf7cb433ce80e854b0f964273f2e5c9ec50db39c4c9e27a4704a34 |

### 1.1 Requirement Language

The words **MUST**, **MUST NOT**, **SHALL**, **SHALL NOT**, **SHOULD**, **SHOULD NOT**, **REQUIRED**, **RECOMMENDED**, **MAY**, and **OPTIONAL** in this document follow their conventional interpretation for requirements specifications. All requirements are stated in the context of a fictional mock feature.

### 1.2 Evidence Labels

- **[CONFIRMED MOCK FACT]** — Statement supported by evidence retrieved from the knowledge base.
- **[ASSUMPTION]** — Statement that is plausible but not confirmed by retrieved evidence; requires validation before implementation.
- **[OPEN QUESTION]** — Decision that must be resolved before the feature is considered ready for development.

## 2. Executive Summary

The fictional SaaS admin console provides a CSV Batch User Import feature that allows authorized administrators to upload a UTF-8 CSV containing up to 10,000 user records. The system validates and imports the file asynchronously, presents job-level progress and results, supports a downloadable row-level error report, and requires idempotent request handling. The initial release supports CSV only; XLSX files and end-user self-service imports are explicitly excluded.

This enhancement introduces a **validation-preview step** into the existing import workflow. After an administrator uploads a CSV file, the system parses and validates all rows, then presents a summary of all validation errors along with the specific affected rows. The administrator must explicitly confirm or cancel the import before any user records are created or modified. This reduces partial imports, wasted time, and manual cleanup caused by errors discovered only after processing has begun.

All existing capabilities — 10,000-row maximum per file, asynchronous import processing, downloadable error report, and idempotent import behavior — are preserved.

## 3. Background and Problem

### 3.1 Background

The fictional SaaS admin console currently requires administrators to create users individually, which is slow and error-prone for organizations onboarding or updating large groups of users. A CSV Batch User Import feature was introduced to allow authorized administrators to submit up to 10,000 user records in a single UTF-8 CSV file. The system validates and imports the file asynchronously, provides job-level progress and results, supports a downloadable row-level error report, and enforces idempotent request handling. CSV is the only supported format; XLSX is explicitly excluded.

The current import flow uploads and processes the CSV in a single step without a validation-preview gate. Administrators submit the file, the system begins asynchronous processing, and errors are only surfaced after the import has already been attempted.

### 3.2 Problem Statement

Administrators who need to create or update many users discover validation errors only after the import has already been attempted. This can result in partial imports, wasted time, and the need for manual cleanup. There is no opportunity to review the full set of validation errors and affected rows before records are persisted.

Administrators need a pre-commit validation preview so they can inspect all errors and affected rows, then make an informed decision to proceed with or abort the import. The asynchronous workflow must continue to accept a supported CSV file, validate file-level and row-level constraints, provide progress and final status, import valid rows according to an explicitly defined atomicity policy, produce an actionable error report, and prevent duplicate effects when requests are retried.

## 4. Goals

| ID | Goal |
|---|---|
| G-01 | Allow authorized administrators to submit up to 10,000 user records in one UTF-8 CSV file. |
| G-02 | Validate and import submitted records asynchronously. |
| G-03 | Introduce a validation-preview step that surfaces all validation errors and affected rows before any records are persisted. |
| G-04 | Require explicit administrator confirmation after preview before any user mutations occur. |
| G-05 | Allow administrators to cancel an import after preview with zero side effects. |
| G-06 | Provide clear job status, aggregate results, and row-level remediation guidance. |
| G-07 | Provide a downloadable error report for rejected rows after import execution. |
| G-08 | Ensure retries do not create duplicate import jobs or duplicate user mutations. |
| G-09 | Apply the same authorization, tenant-isolation, audit, and user-validation controls used by existing user-management workflows. |
| G-10 | Give Support and Operations sufficient metadata to diagnose failures without exposing unnecessary personal data. |

## 5. Non-Goals

The following items are explicitly out of scope for this enhancement:

- **XLSX file support.** The system MUST NOT accept XLSX files.
- **End-user self-service imports.** Only system administrators with existing batch-import permissions may use this feature.
- **Replacing the downloadable error report.** The validation preview is additive; the post-import error report continues to function as it does today.
- **Synchronous full-import processing.** Import execution after confirmation remains asynchronous.
- **Changing the 10,000-row limit.** The existing row cap is preserved.
- **All-or-nothing atomicity change.** The atomicity policy (partial-success or all-or-nothing) remains governed by OQ-02 and is not altered by this enhancement.
- **Real-time collaborative editing of CSV files within the console.**

## 6. Personas

### 6.1 Organization Administrator

The primary user of the CSV batch import feature. This persona is responsible for bulk user provisioning and management. They need to upload CSV files, review validation results before committing, confirm or cancel imports, monitor progress, and download error reports for remediation.

### 6.2 Support Specialist

A support team member who assists administrators experiencing import issues. They need access to job metadata, state history, and diagnostic information to troubleshoot failures without accessing raw CSV contents or unnecessary personal data.

### 6.3 Security or Compliance Reviewer

A reviewer who evaluates authorization, auditability, retention, tenant isolation, and handling of personal data. They need to verify that only authorized administrators can import users, trace who submitted an import and what changes resulted, confirm that files and reports expire according to policy, and confirm that one tenant cannot access another tenant's jobs or files.

## 7. Scope

### 7.1 In Scope

- CSV template download.
- UTF-8 CSV upload.
- File size, encoding, header, schema, and row-count validation.
- Maximum of 10,000 data rows per file.
- **Validation-preview step** — parse and validate all rows, present a summary of all validation errors and affected rows before any mutations.
- **Explicit confirmation gate** — administrator must confirm or cancel after reviewing the preview.
- **Temporary storage of validated file state** — so confirmation references already-validated data without a second upload.
- Asynchronous validation and import (import execution begins only after confirmation).
- Job creation, status retrieval, progress display, and final summary.
- Row-level validation.
- Duplicate detection within the file.
- Existing-user conflict handling.
- Idempotent job submission and row mutation extended to cover the two-step flow.
- Downloadable CSV error report (post-import).
- Authorization, tenant isolation, auditing, retention, observability, and rate limiting.
- Administrator cancellation before import mutations begin.

### 7.2 Out of Scope

- XLSX or other non-CSV file formats.
- End-user self-service imports.
- Modifying the 10,000-row limit.
- Replacing the post-import downloadable error report with the preview.
- Real-time collaborative CSV editing.

### 7.3 Proposed Supported CSV Schema

| Column | Required | Type | Rules |
|---|---|---|---|
| `email` | Yes | String | Valid email format; used as primary identity key |
| `first_name` | Yes | String | Non-empty; documented maximum length |
| `last_name` | Yes | String | Non-empty; documented maximum length |
| `role` | Yes | String | Must be a supported role assignable by the submitter |
| `external_id` | No | String | Unique within the file when present; documented maximum length |
| `department` | No | String | Documented maximum length |
| `title` | No | String | Documented maximum length |

### 7.4 Example CSV

```csv
email,first_name,last_name,role,external_id,department,title
jane.doe@example.com,Jane,Doe,admin,EMP-001,Engineering,Staff Engineer
john.smith@example.com,John,Smith,member,EMP-002,Sales,Account Executive
```

## 8. User Flow

1. An authorized administrator opens **Admin Console → Users → Import users**.
2. The administrator downloads a CSV template or reviews formatting instructions.
3. The administrator selects a UTF-8 CSV file.
4. The client performs advisory checks for extension, approximate size, and empty files.
5. The administrator initiates the upload.
6. The client submits an idempotent upload request.
7. The server performs authoritative file-level validation (encoding, schema, headers, row count).
8. If file-level validation fails, the job ends without importing any rows and the administrator is notified.
9. If file-level validation succeeds, the system validates all rows.
10. **The system presents a validation preview** summarizing all validation errors and identifying affected rows with error details.
11. **The administrator reviews the preview.** The preview includes total rows, valid rows, invalid rows, and per-row error details.
12. **The administrator explicitly confirms or cancels the import.**
    - If **canceled**, no user records are created or updated, and any temporary state is cleaned up.
    - If **confirmed**, the system begins asynchronous import processing of validated rows.
13. The administrator may leave the page and return later.
14. The console displays current job status and aggregate progress.
15. When processing completes, the console displays total, successful, failed, and skipped row counts.
16. If one or more rows fail during import, the administrator downloads an error report.
17. The administrator corrects rejected rows and submits a new file using a new idempotency key.

## 9. Job State Model

### 9.1 States

| State | Meaning | Terminal |
|---|---|---:|
| `UPLOADING` | File transfer or upload finalization is in progress | No |
| `QUEUED` | File was accepted and awaits processing | No |
| `VALIDATING` | File-level or row-level validation is running | No |
| `AWAITING_CONFIRMATION` | Validation complete; preview available; awaiting administrator confirmation or cancellation | No |
| `IMPORTING` | Confirmed — validated rows are being applied asynchronously | No |
| `COMPLETED` | All eligible rows were processed successfully | Yes |
| `COMPLETED_WITH_ERRORS` | Processing completed, but one or more rows failed or were skipped | Yes |
| `FAILED` | A job-level failure prevented normal completion | Yes |
| `CANCEL_REQUESTED` | Cancellation was accepted and is being applied | No |
| `CANCELED` | Processing stopped before prohibited mutations occurred | Yes |

### 9.2 State Rules

- `UPLOADING` → `QUEUED` — upload finalized successfully.
- `QUEUED` → `VALIDATING` — worker picks up the job.
- `VALIDATING` → `AWAITING_CONFIRMATION` — all rows validated; preview is ready.
- `VALIDATING` → `FAILED` — file-level validation failure (e.g., encoding, headers, row count).
- `AWAITING_CONFIRMATION` → `IMPORTING` — administrator confirms the import.
- `AWAITING_CONFIRMATION` → `CANCELED` — administrator cancels after preview.
- `AWAITING_CONFIRMATION` → `CANCELED` — confirmation timeout expires (if a TTL is configured).
- `IMPORTING` → `COMPLETED` — all rows processed successfully.
- `IMPORTING` → `COMPLETED_WITH_ERRORS` — processing finished with row failures.
- `IMPORTING` → `FAILED` — permanent system failure during import.
- Any non-terminal state → `CANCEL_REQUESTED` → `CANCELED` — administrator cancels before mutations begin.
- `CANCEL_REQUESTED` → `CANCELED` — cancellation finalized.
- Terminal states are immutable; no further transitions are permitted.

## 10. Functional Requirements

### FR-001: Access Control

Only administrators with the existing batch-import permission may access the import workflow, including upload, preview, confirmation, cancellation, progress, error-report download, and job history.

**Acceptance criteria:**
1. An unauthenticated request returns `401`.
2. An authenticated user without the required permission receives `403`.
3. Permission checks apply to every API endpoint in the import lifecycle.

### FR-002: Template Download

The console MUST provide a downloadable CSV template that matches the current supported schema version.

**Acceptance criteria:**
1. The template contains all supported headers in the documented order.
2. The template is UTF-8 encoded.
3. The schema version is discoverable from the template or its download metadata.

### FR-003: File Selection and Advisory Client Validation

The client MUST perform advisory checks before upload.

**Acceptance criteria:**
1. A file with a non-`.csv` extension triggers a client-side warning.
2. A file exceeding a documented advisory size limit triggers a warning.
3. An empty file selection is prevented.
4. Advisory checks do not replace server-side authoritative validation.

### FR-004: UTF-8 CSV Parsing

The server MUST parse valid UTF-8 CSV using a deterministic CSV parser.

**Acceptance criteria:**
1. A UTF-8 CSV with quoted commas, escaped quotation marks, and CRLF line endings is parsed correctly.
2. A file containing invalid UTF-8 byte sequences fails file-level validation and imports zero rows.
3. UTF-8 byte-order mark handling follows the documented parsing policy. **[OPEN QUESTION]**
4. Inconsistent column counts cause affected rows or the entire file to fail according to the documented malformed-row policy.

### FR-005: File Type Enforcement

The server MUST reject non-CSV files.

**Acceptance criteria:**
1. An XLSX file is rejected with an appropriate error before any processing.
2. Any file whose content does not conform to CSV is rejected.
3. Rejection results in zero user mutations.

### FR-006: Row Limit

An import MUST contain between 1 and 10,000 data rows, excluding the header.

**Acceptance criteria:**
1. A CSV containing exactly 1 data row passes row-count validation.
2. A CSV containing exactly 10,000 data rows passes row-count validation.
3. A CSV containing 10,001 data rows fails file-level validation.
4. A header-only CSV fails file-level validation.
5. A row-limit failure results in zero user mutations.

### FR-007: Header Validation

The server MUST validate CSV headers before row processing.

**Acceptance criteria:**
1. A file missing any required header fails file-level validation.
2. A file containing the same header more than once fails file-level validation.
3. A file containing an unknown header fails under the proposed strict-schema policy.
4. Header matching follows a documented case-sensitivity and whitespace policy. **[OPEN QUESTION]**
5. A header-validation failure results in zero user mutations.

### FR-008: Row Validation

Each data row MUST be validated against field and business rules before mutation.

**Acceptance criteria:**
1. A row with an invalid email address is rejected with a stable error code identifying `email`.
2. A row missing a required name field is rejected with the applicable field error.
3. A row containing an unsupported role is rejected with a `ROLE_INVALID` error.
4. A row assigning a role beyond the submitter's authority is rejected with `ROLE_NOT_ASSIGNABLE`.
5. A row exceeding a documented field length is rejected before persistence.
6. A valid row receives no validation errors and becomes eligible for import.
7. **All row validation errors are surfaced in the validation preview before the administrator confirms.**

### FR-009: Duplicate Detection Within a File

The system MUST detect duplicate identities in the same CSV.

**Acceptance criteria:**
1. Two rows with equal normalized emails are flagged with a duplicate-email error or handled by a documented deterministic winner policy.
2. Two rows with the same non-empty `external_id` are reported.
3. Duplicate comparison applies the same normalization rules used by user persistence.
4. Duplicate rows never create more than one user mutation.
5. **Duplicate errors are visible in the validation preview.**

### FR-010: Existing-User Conflict Handling

The system MUST handle conflicts with existing users according to the documented mode (e.g., `CREATE_ONLY`).

**Acceptance criteria:**
1. Under `CREATE_ONLY`, a row matching an existing user is skipped or rejected.
2. The conflict is surfaced in the validation preview with the affected row identified.
3. The import mode is documented and configurable per OQ-01.

### FR-011: Asynchronous Job Creation

A successful submission MUST return without waiting for full validation or import completion.

**Acceptance criteria:**
1. A valid submission returns `202 Accepted`.
2. The response includes a job identifier, initial state, creation timestamp, and status URL.
3. The response does not imply that row validation or import has completed.
4. The job remains retrievable after the initiating browser session ends.
5. Background processing can start independently of the request connection.

### FR-012: Import Progress

The administrator MUST be able to view job progress.

**Acceptance criteria:**
1. A non-terminal job response contains its current state.
2. During row processing, the response contains `processed_rows` and `total_rows`.
3. `processed_rows` is never negative, never exceeds `total_rows`, and never decreases.
4. The console refreshes status without requiring a full page reload.
5. Refresh failures display a retryable status message without creating another import.

### FR-013: Partial-Success Processing

**[ASSUMPTION]** After file-level validation succeeds and the administrator confirms, valid rows MUST be imported even when other rows are invalid.

**Acceptance criteria:**
1. A file with one valid and one invalid row results in the valid row imported and the invalid row rejected.
2. The terminal job state is `COMPLETED_WITH_ERRORS`.
3. `successful_rows` equals the count of valid rows and `failed_rows` equals the count of invalid rows.
4. Invalid rows appear in the error report.
5. A row-level validation failure does not roll back unrelated successful rows.

### FR-014: Idempotent Submission

Job submission MUST be idempotent, extended to cover the two-step (upload → confirm) flow.

**Acceptance criteria:**
1. The same tenant, authenticated principal, endpoint, idempotency key, and identical request payload, when submitted more than once within the retention window, all reference the same job identifier.
2. Duplicate submissions create at most one processing job.
3. Duplicate submissions create no duplicate user mutations.
4. The same idempotency key with a materially different payload returns `409 Conflict`.
5. Idempotency scope and retention duration are documented and enforced.
6. **Re-confirming an already-confirmed job does not create duplicate mutations.**

### FR-015: Idempotent Row Mutation

Individual row mutations MUST be idempotent.

**Acceptance criteria:**
1. If a worker retries a row that was already successfully mutated, no duplicate user record is created.
2. Row-level idempotency keys or operation keys prevent duplicate effects.

### FR-016: Final Summary

Upon completion, the console MUST display a final summary including total, successful, failed, and skipped row counts.

**Acceptance criteria:**
1. Aggregate counts are consistent with individual row outcomes.
2. The summary is accessible for the retention duration of the job.

### FR-017: Downloadable Error Report

Jobs containing row failures MUST provide a downloadable error report after import execution.

**Acceptance criteria:**
1. A completed job with at least one failed or skipped row allows the administrator to download a UTF-8 CSV error report.
2. The report includes source row number, stable error code, affected field when applicable, and human-readable remediation text.
3. The report includes enough original row values to correct and retry the row, subject to privacy policy.
4. The report excludes rows that completed successfully unless explicitly documented otherwise.
5. A job with no row errors generates no error report.
6. An expired report returns `410 Gone` or another documented expiration response.
7. **The validation preview does not replace this post-import error report.**

### FR-018: Job History

**[ASSUMPTION]** The console SHOULD show recent import jobs for the current tenant.

**Acceptance criteria:**
1. The history lists only jobs belonging to the active tenant.
2. Each entry includes job identifier, submitter, filename, state, timestamps, and aggregate counts.
3. Results use deterministic pagination and ordering.
4. Filtering by terminal state returns only matching jobs.
5. Raw file contents are not exposed in list responses.

### FR-019: Cancellation

The administrator MUST be able to cancel an import before mutations begin.

**Acceptance criteria:**
1. Cancellation from the `AWAITING_CONFIRMATION` state results in `CANCELED` with zero user mutations.
2. Cancellation from `QUEUED` or `VALIDATING` transitions through `CANCEL_REQUESTED` to `CANCELED`.
3. Cancellation after import mutations have started follows a documented boundary policy.
4. A canceled job retains metadata for audit and history purposes.

### FR-020: Audit Logging

Security-relevant import activity MUST be auditable.

**Acceptance criteria:**
1. Job submission records actor, tenant, job identifier, timestamp, source interface, and outcome.
2. **Confirmation and cancellation after preview** record actor, job identifier, timestamp, and result.
3. Completion records aggregate counts and terminal state.
4. Error-report download records actor, job identifier, timestamp, and outcome.
5. Audit records do not store raw CSV contents or full error reports.
6. Audit access follows the fictional product's existing authorization policy.

### FR-021: Retry of System Failures

Transient system failures MUST be retried with bounded backoff.

**Acceptance criteria:**
1. A transient dependency failure during validation or import triggers automatic retry.
2. Retry count and backoff parameters are documented and bounded.
3. A permanent failure transitions the job to `FAILED` with diagnostic metadata.
4. Retries respect row-level idempotency.

## 11. Error Handling

### 11.1 Error Categories

| Category | Scope | Example behavior |
|---|---|---|
| Authentication | Request | Return `401`; create no job |
| Authorization | Request | Return `403`; create no job |
| Tenant isolation | Request/resource | Return non-disclosing `404` |
| Unsupported file type | File | Reject before processing |
| Encoding error | File | Mark job `FAILED`; import zero rows |
| Schema/header error | File | Mark job `FAILED`; import zero rows |
| Row-count violation | File | Mark job `FAILED`; import zero rows |
| Field validation | Row | Surface in validation preview; reject row during import; continue eligible rows |
| Duplicate in file | Row | Surface in validation preview; reject according to deterministic policy |
| Existing user conflict | Row | Surface in validation preview; skip/reject under create-only policy |
| Confirmation timeout | Job | Transition `AWAITING_CONFIRMATION` → `CANCELED`; import zero rows |
| Transient dependency failure | Job or row | Retry with bounded backoff |
| Permanent system failure | Job | Mark `FAILED`; preserve diagnostic metadata |
| Expired artifact | Download | Return `410 Gone` |
| Idempotency conflict | Request | Return `409 Conflict` |

### 11.2 Error Response Shape

```json
{
  "error": {
    "code": "STABLE_ERROR_CODE",
    "message": "Human-readable description of what happened.",
    "correlation_id": "corr_example_01",
    "retryable": false,
    "details": [
      {
        "field": "affected_field",
        "reason": "SPECIFIC_REASON"
      }
    ]
  }
}
```

All identifiers above are illustrative.

### 11.3 Error-Message Requirements

- Messages MUST state what happened and, when safe, how to correct it.
- Messages MUST NOT expose stack traces, internal hostnames, credentials, storage locations, or cross-tenant resource existence.
- Stable machine-readable codes MUST be separate from localized display text.
- Retry guidance MUST accurately reflect whether retrying can succeed without changing input.
- Row numbers MUST refer consistently to physical CSV lines or logical data-row numbers; the selected convention must be documented. **[OPEN QUESTION]**
- **Validation-preview errors MUST use the same error codes and message format as the post-import error report** to ensure consistency.

## 12. Data Model

### 12.1 `UserImportJob`

| Field | Type | Description |
|---|---|---|
| `job_id` | UUID/string | Globally unique opaque identifier |
| `tenant_id` | String | Tenant ownership boundary |
| `submitted_by` | String | Authorized administrator identifier |
| `original_filename` | String | Sanitized display filename |
| `file_digest` | String | Cryptographic digest for integrity and idempotency checks |
| `schema_version` | String | CSV schema version |
| `state` | Enum | Current job state (includes `AWAITING_CONFIRMATION`) |
| `total_rows` | Integer | Data-row count |
| `processed_rows` | Integer | Rows reaching a recorded outcome |
| `successful_rows` | Integer | Successful mutations |
| `failed_rows` | Integer | Rejected rows |
| `skipped_rows` | Integer | Rows intentionally not mutated |
| `preview_valid_rows` | Integer | Count of rows passing validation in preview |
| `preview_invalid_rows` | Integer | Count of rows failing validation in preview |
| `preview_generated_at` | Nullable timestamp | When the validation preview was generated |
| `confirmed_at` | Nullable timestamp | When the administrator confirmed the import |
| `confirmed_by` | Nullable string | Administrator who confirmed |
| `idempotency_key_hash` | String | Non-reversible representation of the request key |
| `request_fingerprint` | String | Canonical request fingerprint |
| `failure_code` | Nullable string | Job-level failure category |
| `error_report_ref` | Nullable string | Internal artifact reference, not a public URL |
| `preview_ref` | Nullable string | Internal reference to stored validation-preview data |
| `created_at` | Timestamp | Job creation time |
| `import_started_at` | Nullable timestamp | When import processing began |
| `completed_at` | Nullable timestamp | When the job reached a terminal state |

### 12.2 `UserImportRowResult`

| Field | Type | Description |
|---|---|---|
| `job_id` | String | Parent job reference |
| `row_number` | Integer | Physical or logical row number |
| `email` | String | Email from the source row |
| `external_id` | Nullable string | External ID from the source row |
| `status` | Enum | `VALID`, `INVALID`, `IMPORTED`, `FAILED`, `SKIPPED` |
| `error_code` | Nullable string | Stable error code if validation failed |
| `error_field` | Nullable string | Field that caused the error |
| `error_message` | Nullable string | Human-readable remediation text |
| `row_operation_key` | String | Idempotency key for the row mutation |
| `validated_at` | Timestamp | When the row was validated |
| `imported_at` | Nullable timestamp | When the row mutation completed |

### 12.3 `UserImportIdempotencyRecord`

| Field | Type | Description |
|---|---|---|
| `tenant_id` | String | Tenant scope |
| `principal_id` | String | Caller scope |
| `idempotency_key_hash` | String | Hashed key |
| `request_fingerprint` | String | Canonical request fingerprint |
| `job_id` | String | Original job |
| `created_at` | Timestamp | First accepted request |
| `expires_at` | Timestamp | Key-retention expiry |

### 12.4 Data Integrity Constraints

- A job belongs to exactly one tenant.
- A row result belongs to exactly one job.
- `(job_id, row_number)` must be unique.
- `row_operation_key` must be unique within its defined mutation scope.
- The idempotency tuple must be unique for its retention window.
- Aggregate counts must be derivable from row outcomes or reconciled against them.
- Tenant identifiers must be included in every resource lookup.
- State transitions must use concurrency control.
- **Validation-preview data (`preview_ref`) must be retained until the job transitions out of `AWAITING_CONFIRMATION` or a documented TTL expires.**

## 13. API Contract

### 13.1 Create Import Job

`POST /mock-api/v1/admin/user-imports`

Headers:

```http
Authorization: Bearer <fictional-token>
Idempotency-Key: <opaque-client-generated-value>
Content-Type: multipart/form-data
```

Request parts:

| Part | Required | Description |
|---|---|---|
| `file` | Yes | UTF-8 CSV |
| `schema_version` | Yes | Requested CSV schema version |
| `mode` | Yes | Initial proposal: `CREATE_ONLY` |

Response:

```http
HTTP/1.1 202 Accepted
Location: /mock-api/v1/admin/user-imports/job_mock_01
```

```json
{
  "job_id": "job_mock_01",
  "state": "QUEUED",
  "schema_version": "1",
  "created_at": "2026-09-15T03:23:38Z",
  "status_url": "/mock-api/v1/admin/user-imports/job_mock_01"
}
```

The system processes validation asynchronously. Once validation completes, the job transitions to `AWAITING_CONFIRMATION`. The administrator retrieves the job to view the preview and then confirms or cancels.

**Confirm Import:**

`POST /mock-api/v1/admin/user-imports/{job_id}/confirm`

No request body is required. Confirms the import after preview. Response: `200 OK` with updated job state `IMPORTING`.

**Cancel Import (from preview):**

See section 13.5.

### 13.2 Retrieve Job

`GET /mock-api/v1/admin/user-imports/{job_id}`

When the job is in `AWAITING_CONFIRMATION`, the response includes validation-preview summary fields:

```json
{
  "job_id": "job_mock_01",
  "state": "AWAITING_CONFIRMATION",
  "original_filename": "users.csv",
  "total_rows": 500,
  "preview_valid_rows": 485,
  "preview_invalid_rows": 15,
  "preview_generated_at": "2026-09-15T03:24:05Z",
  "validation_errors": [
    {
      "row_number": 14,
      "email": "invalid@example.invalid",
      "error_code": "ROLE_INVALID",
      "error_field": "role",
      "error_message": "The specified role is not supported."
    }
  ],
  "links": {
    "self": "/mock-api/v1/admin/user-imports/job_mock_01",
    "confirm": "/mock-api/v1/admin/user-imports/job_mock_01/confirm",
    "cancel": "/mock-api/v1/admin/user-imports/job_mock_01/cancel"
  }
}
```

### 13.3 List Jobs

`GET /mock-api/v1/admin/user-imports`

Returns a paginated list of import jobs for the current tenant. Supports filtering by state.

### 13.4 Download Error Report

`GET /mock-api/v1/admin/user-imports/{job_id}/error-report`

Successful response:

```http
HTTP/1.1 200 OK
Content-Type: text/csv; charset=utf-8
Content-Disposition: attachment; filename="user-import-errors-job_mock_01.csv"
```

Proposed columns:

```csv
row_number,email,external_id,error_code,error_field,error_message
14,invalid@example.invalid,EMP-14,ROLE_INVALID,role,The specified role is not supported.
```

### 13.5 Cancel Job

`POST /mock-api/v1/admin/user-imports/{job_id}/cancel`

Cancels a job. When called from `AWAITING_CONFIRMATION`, immediately transitions to `CANCELED` with zero user mutations.

### 13.6 API Semantics

- All endpoints require authentication and authorization.
- Tenant isolation is enforced on every request; a job belonging to another tenant returns `404`.
- `POST` endpoints that create or mutate jobs are idempotent when the `Idempotency-Key` header is provided.
- Error responses follow the shape defined in section 11.2.
- Pagination uses cursor-based tokens with documented ordering.
- Rate limiting is applied per tenant.

## 14. Security and Privacy

### 14.1 Authorization

All import operations — upload, preview retrieval, confirmation, cancellation, progress, error-report download, and job history — require the existing batch-import administrator permission. No new permission scopes are introduced by the validation-preview enhancement.

### 14.2 Tenant Isolation

Every API request and background operation MUST include the tenant identifier in resource lookups. A request referencing a job belonging to a different tenant MUST receive a non-disclosing `404` response. Cross-tenant data leakage MUST be prevented in validation previews, error reports, job metadata, and audit logs.

### 14.3 Data Protection

- CSV files, validation-preview data, and error reports contain personal data and MUST be encrypted in transit and at rest.
- Temporary artifacts (including validation-preview state) MUST have time-bounded retention.
- Access to raw files, preview data, and reports MUST be limited to explicitly authorized principals and processing components.
- Download responses SHOULD use short-lived authorization rather than indefinitely reusable public links.
- Logs MUST NOT contain full CSV rows, invitation tokens, or unnecessary personal data.
- Email addresses SHOULD be redacted or hashed in operational telemetry where full values are unnecessary.

### 14.4 File Safety

- Uploaded files MUST be validated for content type and encoding before processing.
- CSV output (error reports, validation previews) MUST be formula-injection-safe.
- File names MUST be sanitized before storage and display.

### 14.5 Privacy

- Personal data in CSV files, previews, and reports is subject to the product's data-processing agreement.
- Retention and deletion of import artifacts MUST follow the documented privacy policy.
- Analytics events MUST NOT include personal data (see section 16.3).

### 14.6 Audit and Abuse Prevention

- All security-relevant actions are logged per FR-020, including the new confirmation and cancellation steps.
- Rate limiting is applied to prevent abuse of upload, confirm, and cancel endpoints.
- Failed authentication and authorization attempts are logged.

## 15. Non-Functional Requirements

### NFR-001: Capacity

The system MUST accept up to 10,000 data rows per import. A 10,000-row valid CSV MUST be accepted without request timeout. Processing remains asynchronous regardless of row count. Peak memory usage remains within a documented limit under the approved deployment profile.

### NFR-002: Performance

**[ASSUMPTION]** Performance objectives:

- Job creation API: p95 under 2 seconds, excluding client upload transfer time.
- Job-status API: p95 under 500 milliseconds.
- **Validation preview generation for a 10,000-row file: p95 under 60 seconds.** **[ASSUMPTION]**
- A 10,000-row import (post-confirmation): 95% complete within 15 minutes under nominal load.
- Console progress: no more than 15 seconds behind persisted job state under nominal conditions.

These values require load testing and capacity validation.

### NFR-003: Availability and Durability

- Job state, row results, and validation-preview data MUST survive single-component failures.
- Accepted jobs MUST be durable and recoverable after transient infrastructure issues.
- Temporary preview data MUST remain available until the administrator confirms or cancels, or the TTL expires.

### NFR-004: Scalability

- The system MUST support concurrent import jobs across tenants without interference.
- Validation and import processing MUST scale horizontally.
- The validation-preview step MUST NOT create a bottleneck that degrades throughput for other tenants.

### NFR-005: Observability

- Job creation, state transitions (including `AWAITING_CONFIRMATION`), confirmation, cancellation, completion, and failure MUST emit structured metrics and logs.
- Stuck-job alerts MUST fire if a job remains in `AWAITING_CONFIRMATION` beyond a configured threshold.
- Error rates, latency percentiles, and queue depth MUST be dashboarded.

### NFR-006: Accessibility

**[ASSUMPTION]** The console MUST meet WCAG 2.2 Level AA for the import workflow, including the validation-preview screen.

- The workflow is operable by keyboard.
- Status changes and validation-preview results are announced through appropriate live regions.
- Errors are associated programmatically with affected controls.
- Progress is not conveyed by color alone.
- Confirm, cancel, download, and retry actions have descriptive accessible names.

### NFR-007: Compatibility

- The import workflow, including validation preview, MUST function in the console's supported browsers.
- The API contract MUST be versioned to allow backward-compatible evolution.

### NFR-008: Localization

- Error messages and UI labels MUST support the console's existing localization framework.
- Stable error codes remain language-independent.

### NFR-009: Maintainability

- Parsing, validation, preview generation, mutation, and reporting MUST be separable components.
- Validation rules MUST have automated tests.
- Job-state transitions (including the new `AWAITING_CONFIRMATION` state) MUST be centrally defined and tested.
- API contracts and CSV schema versions MUST be documented.
- Operational runbooks MUST cover stuck, failed, and high-volume jobs, as well as jobs stuck in `AWAITING_CONFIRMATION`.

### NFR-010: Recovery

- A job that fails during validation MUST transition to `FAILED` and be retryable via a new submission.
- A job stuck in `AWAITING_CONFIRMATION` beyond the configured TTL MUST transition to `CANCELED`.
- Worker crashes MUST NOT leave jobs in an unrecoverable state.
- Recovery procedures MUST be documented in operational runbooks.

## 16. Analytics and Success Metrics

### 16.1 Product Metrics

| Metric | Definition | Proposed target | Status |
|---|---|---|---|
| Adoption | Tenants using batch import among tenants creating ≥ 50 users/month | 30% within 90 days | **[ASSUMPTION]** |
| Import completion rate | Jobs reaching `COMPLETED` or `COMPLETED_WITH_ERRORS` divided by confirmed jobs | ≥ 98% | **[ASSUMPTION]** |
| First-pass row success | Successful rows divided by submitted rows on first attempt | ≥ 90% | **[ASSUMPTION]** |
| Preview-to-confirm rate | Jobs confirmed divided by jobs reaching `AWAITING_CONFIRMATION` | Establish baseline | **[ASSUMPTION]** |
| Preview cancellation rate | Jobs canceled from `AWAITING_CONFIRMATION` divided by jobs reaching preview | Track and analyze | **[ASSUMPTION]** |
| Time saved | Estimated administrator time avoided compared with manual creation | Establish through research | **[OPEN QUESTION]** |
| Error remediation | Failed rows successfully imported within seven days | ≥ 70% | **[ASSUMPTION]** |
| Duplicate-effect incidents | Confirmed duplicate users caused by request or worker retries | 0 | Required |
| Cross-tenant exposure | Confirmed unauthorized cross-tenant access | 0 | Required |

### 16.2 Operational Metrics

- Validation-preview generation latency (p50, p95, p99).
- Time spent in `AWAITING_CONFIRMATION` state (p50, p95).
- Confirmation-to-import-start latency.
- Import processing duration (p50, p95, p99).
- Job failure rate by failure category.
- Queue depth and worker utilization.
- Error-report generation latency.

### 16.3 Analytics Privacy

- Analytics events MUST NOT include personal data such as email addresses, names, or external IDs.
- Row counts are transmitted as bands (e.g., 1–100, 101–1000, 1001–10000) to reduce fingerprinting risk.
- Tenant identifiers in analytics MUST be pseudonymized where feasible.

### 16.4 Proposed Analytics Events

| Event | Trigger | Allowed properties |
|---|---|---|
| `user_import_page_viewed` | Import page opened | Tenant-safe product context |
| `user_import_template_downloaded` | Template downloaded | Schema version |
| `user_import_submitted` | Job accepted | Job ID, row-count band, schema version |
| `user_import_preview_viewed` | Validation preview displayed | Job ID, valid-row band, invalid-row band |
| `user_import_confirmed` | Administrator confirms import after preview | Job ID, time-in-preview |
| `user_import_preview_canceled` | Administrator cancels from preview | Job ID, time-in-preview, invalid-row band |
| `user_import_completed` | Terminal success state | Job ID, duration, aggregate counts |
| `user_import_failed` | Job-level failure | Job ID, failure category |
| `user_import_error_report_downloaded` | Report downloaded | Job ID, error-count band |
| `user_import_canceled` | Job canceled (any state) | Job ID, prior state |

## 17. Code Impact

All paths below are **simulated** and do not represent actual repository files. They are provided for illustrative planning purposes only.

### 17.1 Simulated Frontend Impact

| Simulated path | Simulated change |
|---|---|
| `[SIMULATED] /fictional-saas/frontend/src/pages/UserImportPage.tsx` | Add validation-preview screen with error summary table, confirm button, and cancel button |
| `[SIMULATED] /fictional-saas/frontend/src/components/ImportPreviewTable.tsx` | New component to render row-level validation errors and affected row details |
| `[SIMULATED] /fictional-saas/frontend/src/api/userImportClient.ts` | Add `confirmImport` and preview-retrieval API calls |
| `[SIMULATED] /fictional-saas/frontend/src/hooks/useImportJob.ts` | Extend polling to handle `AWAITING_CONFIRMATION` state |
| `[SIMULATED] /fictional-saas/frontend/test/` | Add unit and integration tests for preview, confirm, and cancel flows |

### 17.2 Simulated Backend Impact

| Simulated path | Simulated change |
|---|---|
| `[SIMULATED] /fictional-saas/services/user-import-api/src/routes.py` | Add `POST .../confirm` and `POST .../cancel` endpoints; extend retrieve to include preview data |
| `[SIMULATED] /fictional-saas/services/user-import-worker/src/validator.py` | Extract validation logic to produce preview results and store them temporarily |
| `[SIMULATED] /fictional-saas/services/user-import-worker/src/state_machine.py` | Add `AWAITING_CONFIRMATION` state and transitions |
| `[SIMULATED] /fictional-saas/services/user-import-worker/src/preview_store.py` | New module for temporary storage of validated file state |
| `[SIMULATED] /fictional-saas/services/user-import-worker/src/error_report.py` | Generate formula-safe CSV error reports (unchanged) |
| `[SIMULATED] /fictional-saas/services/user-import-worker/test/` | Add parser, validator, preview, confirm, cancel, retry, and idempotency tests |

### 17.3 Simulated Data and Contract Impact

| Simulated path | Simulated change |
|---|---|
| `[SIMULATED] /fictional-saas/database/migrations/` | Add `preview_valid_rows`, `preview_invalid_rows`, `preview_generated_at`, `confirmed_at`, `confirmed_by`, `preview_ref` columns to `UserImportJob`; add `AWAITING_CONFIRMATION` to state enum |
| `[SIMULATED] /fictional-saas/api-specs/user-imports-v1.yaml` | Add confirm and cancel endpoints; extend job schema with preview fields |
| `[SIMULATED] /fictional-saas/api-specs/` | Version the API contract to reflect the two-step flow |

### 17.4 Test Impact

- **Unit tests:** Validation logic, state-machine transitions (including `AWAITING_CONFIRMATION`), preview generation, confirmation idempotency, and cancellation side-effect-free behavior.
- **Integration tests:** End-to-end upload → validate → preview → confirm → import flow; upload → validate → preview → cancel flow; timeout of `AWAITING_CONFIRMATION`; idempotent confirm; concurrent confirm and cancel.
- **Load tests:** 10,000-row validation-preview generation within performance targets; confirmation under concurrent tenant load.
- **Security tests:** Authorization on confirm and cancel endpoints; tenant isolation of preview data; formula-injection safety of preview output.

## 18. Dependencies

| Dependency | Purpose | Status |
|---|---|---|
| Existing CSV batch import feature | Foundation for the validation-preview enhancement | **[ASSUMPTION]** In production |
| Asynchronous job processing infrastructure | Handles background validation and import execution | **[ASSUMPTION]** In production |
| Idempotency mechanism (e.g., idempotency key / file hash) | Extended to cover the two-step confirm flow | **[ASSUMPTION]** In production |
| Temporary storage service | Stores validated file state between preview and confirmation | **[ASSUMPTION]** Available or to be provisioned |
| Feature flag service | Controls rollout of the validation-preview enhancement | **[ASSUMPTION]** In production |
| Audit logging infrastructure | Records confirmation and cancellation events | **[ASSUMPTION]** In production |
| Console frontend framework | Renders the validation-preview UI | **[ASSUMPTION]** In production |

## 19. Rollout and Rollback

### 19.1 Proposed Rollout

1. **Internal development:** Implement the validation-preview step, confirm and cancel flows, and `AWAITING_CONFIRMATION` state using synthetic data. Validate parsing, idempotency, isolation, preview accuracy, and failure recovery.
2. **Security and privacy review:** Approve threat model updates, access controls for confirm/cancel, preview data retention, logging, and report contents.
3. **Internal test tenants:** Enable the validation-preview feature through a server-side feature flag for authorized test administrators.
4. **Limited preview:** Enable for a small set of fictional opt-in tenants with explicit support coverage.
5. **Expanded availability:** Increase tenant coverage after success and reliability gates pass.
6. **General availability:** Enable by default only after operational readiness and support documentation are complete.

### 19.2 Rollout Gates

- No unresolved critical security or privacy findings related to the preview or confirm/cancel flows.
- Cross-tenant authorization tests pass for preview, confirm, and cancel endpoints.
- Submission, confirmation, and row-level idempotency tests pass.
- A 10,000-row validation-preview generation meets approved performance objectives.
- Duplicate-effect count remains zero.
- Error reports and preview outputs are formula-injection-safe.
- Monitoring and stuck-job alerts (including `AWAITING_CONFIRMATION` timeout) are operational.
- Support and rollback runbooks are reviewed and updated.
- Retention and deletion behavior for preview data is verified.

### 19.3 Rollback Strategy

- Disable the validation-preview feature through a server-side feature flag, reverting to the original single-step import flow.
- Preserve status and error-report access for already accepted jobs when safe.
- Jobs in `AWAITING_CONFIRMATION` at the time of rollback transition to `CANCELED`.
- Stop dequeuing new jobs while allowing in-flight row mutations to reach a consistent boundary.
- Do not delete job, audit, or idempotency records as part of an emergency feature disablement.
- If a mutation defect is detected, suspend processing before attempting remediation.
- Re-enable only after root cause, affected scope, data correction, and retry safety are established.

### 19.4 Rollback Acceptance Criteria

1. Disabling the feature prevents new jobs from entering the `AWAITING_CONFIRMATION` state.
2. Existing terminal jobs remain readable according to retention policy.
3. Rollback does not cause accepted jobs to restart from the beginning without idempotency protection.
4. In-flight jobs reach a documented recoverable state.
5. The system can identify potentially affected users by job and row-operation key.
6. Jobs that were in `AWAITING_CONFIRMATION` are transitioned to `CANCELED` without user mutations.

## 20. Risks and Mitigations

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| Validation-preview generation for 10,000 rows exceeds performance targets | Administrators experience unacceptable wait times; adoption suffers | Medium | Load-test preview generation early; set performance budget; consider streaming or chunked preview results |
| Temporary preview data storage grows unbounded | Storage costs increase; system performance degrades | Low | Enforce TTL on preview data; monitor storage utilization; alert on anomalies |
| Administrator abandons session after preview without confirming or canceling | Jobs stuck in `AWAITING_CONFIRMATION` consume resources | Medium | Implement configurable TTL that auto-cancels unconfirmed jobs; alert on stuck jobs |
| Race condition between confirm and cancel requests | Inconsistent state; potential for unintended mutations | Low | Use concurrency control (optimistic locking or compare-and-swap) on state transitions |
| Idempotency mechanism does not cover the two-step flow correctly | Duplicate jobs or mutations from retried confirmations | Low | Extend idempotency tests to cover confirm retries; document idempotency scope for the new endpoint |
| Preview data becomes stale if underlying user data changes between preview and confirmation | Administrator confirms based on outdated conflict information | Medium | Document staleness window; consider re-validating conflicts at import time; surface discrepancies in the error report |
| Rollback leaves jobs in `AWAITING_CONFIRMATION` without a path to completion | Orphaned jobs; administrator confusion | Low | Rollback procedure transitions `AWAITING_CONFIRMATION` jobs to `CANCELED`; documented in runbook |

## 21. Open Questions

| ID | Question | Decision owner | Blocking |
|---|---|---|---:|
| OQ-01 | Is the initial import mode create-only, upsert, or selectable? | Product and Engineering | Yes |
| OQ-02 | Should valid rows import when other rows fail, or must the job be all-or-nothing? | Product | Yes |
| OQ-03 | What is the default and allowed behavior for invitation emails? | Product and Security | Yes |
| OQ-04 | What maximum file byte size complements the 10,000-row limit? | Engineering and Security | Yes |
| OQ-05 | How long are source files, preview data, reports, job metadata, row results, and idempotency records retained? | Privacy, Security, and Legal | Yes |
| OQ-06 | Are UTF-8 byte-order marks accepted? | Engineering | No |
| OQ-07 | Are headers case-sensitive, and is surrounding whitespace ignored? | Product and Engineering | Yes |
| OQ-08 | Are unknown columns rejected or ignored? | Product | Yes |
| OQ-09 | How are duplicate rows within one file reported: reject all duplicates or accept a deterministic first row? | Product | Yes |
| OQ-10 | What is the TTL for jobs in `AWAITING_CONFIRMATION` before auto-cancellation? | Product and Engineering | Yes |
| OQ-11 | Should the validation preview be synchronous (blocking the upload response) or asynchronous (requiring polling)? | Engineering | Yes |
| OQ-12 | Should existing-user conflicts detected at preview time be re-validated at import time to account for data changes? | Product and Engineering | No |
| OQ-13 | What is the maximum size of validation-preview response payload, and should large previews be paginated or streamed? | Engineering | No |

## 22. Evidence Ledger

| Evidence ID | Statement | Classification | Source | Confidence | Impact |
|---|---|---|---|---|---|
| EL-001 | The feature belongs in an existing SaaS admin console. | Confirmed mock fact | Knowledge base (chunk 2a725bfb) | High within mock | Defines product surface |
| EL-002 | Administrators are the actors who upload files. | Confirmed mock fact | Knowledge base (chunk 2a725bfb) | High within mock | Defines primary persona |
| EL-003 | Input files are UTF-8 CSV. | Confirmed mock fact | Knowledge base (chunk 2a725bfb) | High within mock | Defines parser and validation |
| EL-004 | One file may contain up to 10,000 rows. | Confirmed mock fact | Knowledge base (chunk 0f5f9453, 6f404d35) | High within mock | Defines capacity boundary |
| EL-005 | Validation and import are asynchronous. | Confirmed mock fact | Knowledge base (chunk dbc3ff6a) | High within mock | Requires durable job lifecycle |
| EL-006 | A downloadable error report is required. | Confirmed mock fact | Knowledge base (chunk 5e8da2d0) | High within mock | Requires report generation and protected download |
| EL-007 | XLSX is not supported. | Confirmed mock fact | Knowledge base (chunk 2a725bfb) | High within mock | Defines non-goal and rejection behavior |
| EL-008 | Job submission must be idempotent. | Confirmed mock fact | Knowledge base (chunk 8403248f) | High within mock | Requires idempotency mechanism |
| EL-009 | The current import flow lacks a validation-preview gate. | Assumption | User requirement | Medium | Motivates the enhancement |
| EL-010 | The validation preview itself will be synchronous or near-synchronous. | Assumption | User requirement | Medium | Affects UX and architecture |
| EL-011 | Preview results will be stored temporarily for confirmation. | Assumption | User requirement | Medium | Requires temporary storage |
| EL-012 | Partial-success processing is the default atomicity policy. | Assumption | Knowledge base (chunk 7ccb19b2) | Medium | Affects error handling and preview semantics |

## 23. Definition of Ready

The feature is ready for development when all of the following are true:

- All blocking open questions (OQ-01 through OQ-05, OQ-07 through OQ-11) are resolved and decisions are documented.
- The validation-preview UX design (wireframes or mockups) is reviewed and approved.
- The API contract for confirm and cancel endpoints is reviewed by frontend and backend teams.
- The `AWAITING_CONFIRMATION` state, its transitions, and its TTL behavior are agreed upon.
- Security and privacy review of preview data storage and retention is complete.
- Performance targets for validation-preview generation are agreed upon.
- Test strategy covering preview, confirm, cancel, timeout, idempotency, and rollback is documented.
- Dependencies (temporary storage service, feature flag) are confirmed available.
- Acceptance criteria for all functional requirements are reviewed and agreed upon by Product and Engineering.

## 24. Definition of Done

The feature is done when all of the following are true:

- All functional requirements (FR-001 through FR-021) pass their acceptance criteria, including the new validation-preview, confirmation, and cancellation behaviors.
- All non-functional requirements (NFR-001 through NFR-010) are verified, including preview-generation performance and accessibility.
- The `AWAITING_CONFIRMATION` state and all transitions are implemented with concurrency control and tested.
- The confirm and cancel API endpoints are implemented, documented, and tested for idempotency and authorization.
- Temporary preview data storage respects the configured TTL and is cleaned up after confirmation, cancellation, or timeout.
- The downloadable error report continues to function unchanged after import execution.
- Unit, integration, load, and security tests pass as described in section 17.4.
- Analytics events (including `user_import_preview_viewed`, `user_import_confirmed`, `user_import_preview_canceled`) are instrumented and verified.
- Monitoring dashboards and alerts (including `AWAITING_CONFIRMATION` stuck-job alerts) are operational.
- Operational runbooks are updated to cover the new states and rollback procedures.
- Support documentation is updated to describe the validation-preview workflow.
- Rollout gates defined in section 19.2 are satisfied.
- No unresolved critical security, privacy, or accessibility findings.
