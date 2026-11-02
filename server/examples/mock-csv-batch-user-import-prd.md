# MOCK Product Requirements Document: CSV Batch User Import

> **MOCK / NON-AUTHORITATIVE SAMPLE**
>
> This document describes a fictional feature for evaluation and testing purposes only. It is intended to test a PRD-generation system built with Strands and Amazon Bedrock AgentCore. It does not describe an actual product, repository, API, customer commitment, implementation plan, AWS account, or deployed resource.

## 1. Document Control

| Field | Value |
|---|---|
| Document title | CSV Batch User Import |
| Document type | Mock Product Requirements Document |
| Status | Draft — Non-Authoritative |
| Version | 0.1 |
| Last updated | September 15, 2026 |
| Locale | en-US |
| Product | Fictional existing SaaS admin console |
| Intended audience | Product, Design, Engineering, Security, Privacy, Data, Support, and QA |
| Owner | Mock Product Management Team |
| Reviewers | TBD — Mock stakeholders |
| Target release | TBD |
| Classification | Synthetic test content; no production data |
| Authority | None; not approved for implementation |

### 1.1 Requirement Language

The terms **must**, **should**, and **may** indicate requirement priority within this mock document:

- **Must:** Required for initial release.
- **Should:** Important but may be deferred with explicit approval.
- **May:** Optional enhancement.

### 1.2 Evidence Labels

Statements in this document use the following labels:

- **[CONFIRMED MOCK FACT]:** Fixed by the supplied fictional scenario and treated as true within this sample.
- **[ASSUMPTION]:** Added to make the mock PRD complete; requires validation before any real implementation.
- **[OPEN QUESTION]:** Deliberately unresolved and requiring a product or technical decision.

## 2. Executive Summary

The fictional SaaS admin console currently requires administrators to create users individually. This process is slow and error-prone for organizations onboarding or updating large groups of users.

The proposed CSV Batch User Import feature allows authorized administrators to upload a UTF-8 CSV containing up to 10,000 user records. **[CONFIRMED MOCK FACT]** The system validates and imports the file asynchronously, presents job-level progress and results, supports a downloadable row-level error report, and requires idempotent request handling. **[CONFIRMED MOCK FACT]**

The initial release supports CSV only. XLSX files and end-user self-service imports are explicitly excluded. **[CONFIRMED MOCK FACT]**

## 3. Background and Problem

### 3.1 Background

**[ASSUMPTION]** The existing fictional admin console supports manual user creation through a form and exposes user-management APIs used by that form.

**[ASSUMPTION]** Administrators commonly onboard users from HR, identity, or business-operation systems that can export CSV files.

### 3.2 Problem Statement

Administrators who need to create or update many users must repeat the same manual workflow for each person. For large organizations, this can require hours of work and can introduce inconsistent roles, malformed email addresses, duplicate users, and incomplete records.

A synchronous batch operation would create reliability and usability problems for files containing thousands of rows. The system therefore needs an asynchronous workflow that:

1. Accepts a supported CSV file.
2. Validates file-level and row-level constraints.
3. Provides progress and final status.
4. Imports valid rows according to an explicitly defined atomicity policy.
5. Produces an actionable error report.
6. Prevents duplicate effects when requests are retried.

## 4. Goals

| ID | Goal |
|---|---|
| G-01 | Allow authorized administrators to submit up to 10,000 user records in one UTF-8 CSV file. **[CONFIRMED MOCK FACT]** |
| G-02 | Validate and import submitted records asynchronously. **[CONFIRMED MOCK FACT]** |
| G-03 | Provide clear job status, aggregate results, and row-level remediation guidance. |
| G-04 | Provide a downloadable error report for rejected rows. **[CONFIRMED MOCK FACT]** |
| G-05 | Ensure retries do not create duplicate import jobs or duplicate user mutations. **[CONFIRMED MOCK FACT]** |
| G-06 | Apply the same authorization, tenant-isolation, audit, and user-validation controls used by existing user-management workflows. **[ASSUMPTION]** |
| G-07 | Give Support and Operations sufficient metadata to diagnose failures without exposing unnecessary personal data. |

## 5. Non-Goals

The initial release will not:

1. Accept XLSX, XLS, JSON, XML, or compressed archive uploads. **[CONFIRMED MOCK FACT]**
2. Allow end users to import themselves or other users. **[CONFIRMED MOCK FACT]**
3. Provide scheduled, recurring, SFTP-based, or API-feed imports.
4. Synchronize continuously with HR or identity-provider systems.
5. Delete users through CSV.
6. Attach arbitrary custom attributes not defined in the supported schema.
7. Send invitations or welcome emails unless the selected import behavior explicitly enables them. **[OPEN QUESTION]**
8. Provide a general-purpose data-transformation or column-mapping engine.
9. Guarantee that all rows succeed merely because file-level validation passes.
10. Establish or modify billing, seat-allocation, or licensing policy.
11. Claim compatibility with any real repository, service, database, queue, object store, identity provider, or AWS resource.

## 6. Personas

### 6.1 Organization Administrator

**[CONFIRMED MOCK FACT]** The primary actor is an administrator using an existing SaaS admin console.

Needs:

- Import many users efficiently.
- Understand the expected CSV format.
- Detect errors before or during import.
- Know which records succeeded or failed.
- Correct rejected rows and retry safely.
- Avoid unintentionally duplicating users.

### 6.2 Support Specialist

**[ASSUMPTION]** A support specialist helps administrators diagnose failed imports.

Needs:

- Locate a job using a non-sensitive job identifier.
- Review job state, timestamps, error categories, and aggregate counts.
- Avoid viewing raw uploaded files unless explicitly authorized.
- Distinguish user-data errors from system failures.

### 6.3 Security or Compliance Reviewer

**[ASSUMPTION]** A reviewer evaluates authorization, auditability, retention, tenant isolation, and handling of personal data.

Needs:

- Verify that only authorized administrators can import users.
- Trace who submitted an import and what changes resulted.
- Confirm that files and reports expire according to policy.
- Confirm that one tenant cannot access another tenant’s jobs or files.

## 7. Scope

### 7.1 In Scope

- CSV template download.
- UTF-8 CSV upload.
- File size, encoding, header, schema, and row-count validation.
- Maximum of 10,000 data rows per file. **[CONFIRMED MOCK FACT]**
- Asynchronous validation and import. **[CONFIRMED MOCK FACT]**
- Job creation, status retrieval, progress display, and final summary.
- Row-level validation.
- Duplicate detection within the file.
- Existing-user conflict handling.
- Idempotent job submission and row mutation.
- Downloadable CSV error report. **[CONFIRMED MOCK FACT]**
- Authorization, tenant isolation, auditing, retention, observability, and rate limiting.
- Administrator cancellation before import mutations begin. **[ASSUMPTION]**

### 7.2 Out of Scope

- XLSX support.
- End-user self-service.
- User deletion.
- Recurring imports.
- Arbitrary schema mapping.
- Cross-tenant imports.
- Automatic correction of ambiguous data.
- Importing more than 10,000 rows by splitting one upload server-side.
- Editing the source CSV in the console.

### 7.3 Proposed Supported CSV Schema

The following schema is an **[ASSUMPTION]** for this mock PRD:

| Column | Required | Type | Rules |
|---|---:|---|---|
| `email` | Yes | String | Valid email syntax; normalized for comparison; maximum 320 characters |
| `first_name` | Yes | String | Trimmed; 1–100 characters |
| `last_name` | Yes | String | Trimmed; 1–100 characters |
| `role` | Yes | Enum | Must match a role available to the submitting administrator |
| `external_id` | No | String | Unique within the tenant when present; maximum 255 characters |
| `department` | No | String | Maximum 150 characters |
| `send_invitation` | No | Boolean | `true` or `false`; default behavior unresolved |

Unknown columns will be rejected in the initial release. **[ASSUMPTION]**

### 7.4 Example CSV

```csv
email,first_name,last_name,role,external_id,department,send_invitation
alex@example.invalid,Alex,Morgan,member,EMP-1001,Finance,true
sam@example.invalid,Sam,Lee,viewer,EMP-1002,Operations,false
```

The `.invalid` domain is used intentionally; these are synthetic examples.

## 8. User Flow

1. An authorized administrator opens **Admin Console → Users → Import users**. **[ASSUMPTION]**
2. The administrator downloads a CSV template or reviews formatting instructions.
3. The administrator selects a UTF-8 CSV file.
4. The client performs advisory checks for extension, approximate size, and empty files.
5. The administrator reviews the selected filename and starts the import.
6. The client submits an idempotent import request.
7. The server performs authoritative file-level validation.
8. If file-level validation fails, the job ends without importing any rows.
9. If file-level validation succeeds, the system validates all rows asynchronously.
10. **[ASSUMPTION]** Valid rows are imported while invalid rows are rejected, unless the selected atomicity policy changes before implementation.
11. The administrator may leave the page and return later.
12. The console displays current job status and aggregate progress.
13. When processing completes, the console displays total, successful, failed, and skipped row counts.
14. If one or more rows fail, the administrator downloads an error report.
15. The administrator corrects rejected rows and submits a new file using a new idempotency key.
16. Retrying the same original request with the same key returns the original job and creates no duplicate effects.

## 9. Job State Model

### 9.1 States

| State | Meaning | Terminal |
|---|---|---:|
| `UPLOADING` | File transfer or upload finalization is in progress | No |
| `QUEUED` | File was accepted and awaits processing | No |
| `VALIDATING` | File-level or row-level validation is running | No |
| `IMPORTING` | Validated rows are being applied | No |
| `COMPLETED` | All eligible rows were processed successfully | Yes |
| `COMPLETED_WITH_ERRORS` | Processing completed, but one or more rows failed or were skipped | Yes |
| `FAILED` | A job-level failure prevented normal completion | Yes |
| `CANCEL_REQUESTED` | Cancellation was accepted and is being applied | No |
| `CANCELED` | Processing stopped before prohibited mutations occurred | Yes |

### 9.2 State Rules

- States must move forward according to an enforced transition table.
- A terminal state must not transition to a non-terminal state.
- A job-level failure before row mutation must produce zero user changes.
- **[ASSUMPTION]** Cancellation is available only before `IMPORTING`.
- Progress counts must never decrease.
- The sum of terminal row outcomes must equal `total_rows`.

## 10. Functional Requirements

### FR-001: Access Control

Only administrators with a dedicated user-import permission may access or operate the feature.

**Acceptance criteria:**

1. Given a user with `users.import` permission, when the user opens the import route, then the import page is displayed.
2. Given a user without `users.import` permission, when the user opens the import route, then access is denied and no import metadata is returned.
3. Given an unauthorized API caller, when the caller submits a file, then the API returns `403 Forbidden` and creates no job.
4. Given an authorized administrator in Tenant A, when the administrator requests a Tenant B job identifier, then the API returns `404 Not Found` or an equivalent non-disclosing response.

### FR-002: Template Download

The console must provide a downloadable CSV template containing the supported headers.

**Acceptance criteria:**

1. Given an authorized administrator, when the administrator selects **Download template**, then a UTF-8 CSV is downloaded.
2. The template contains each required column exactly once.
3. The template contains only supported columns.
4. The template version is identifiable through response metadata or accompanying documentation.
5. A file created from the unmodified template headers passes header validation.

### FR-003: File Selection and Advisory Client Validation

The client must accept `.csv` files and provide early feedback for obvious unsupported input.

**Acceptance criteria:**

1. Given an `.xlsx` file, when selected, then the client states that XLSX is unsupported and prevents submission.
2. Given an empty file, when selected, then the client prevents submission and displays an actionable message.
3. Given a `.csv` file, when selected, then the client displays its filename and size.
4. Bypassing client validation does not bypass authoritative server validation.

### FR-004: UTF-8 CSV Parsing

The server must parse valid UTF-8 CSV using a deterministic CSV parser.

**Acceptance criteria:**

1. Given a UTF-8 CSV with quoted commas, escaped quotation marks, and CRLF line endings, when validated, then fields are parsed according to the selected CSV standard.
2. Given a file containing invalid UTF-8 byte sequences, when processed, then the job fails file-level validation and imports zero rows.
3. Given a UTF-8 byte-order mark, when processed, then the behavior matches the documented parsing policy. **[OPEN QUESTION]**
4. Given inconsistent column counts, when processed, then affected rows or the entire file fail according to the documented malformed-row policy.

### FR-005: File Type Enforcement

The server must accept CSV only.

**Acceptance criteria:**

1. Given an XLSX file renamed with a `.csv` extension, when inspected, then the server rejects it and creates no user mutations.
2. Given a supported CSV payload with the expected media type, when submitted, then file-type validation passes.
3. Given an unsupported media type, when submitted, then the API returns a documented validation error.
4. The server does not invoke spreadsheet parsing for any submission.

### FR-006: Row Limit

An import must contain between 1 and 10,000 data rows, excluding the header. **[CONFIRMED MOCK FACT]**

**Acceptance criteria:**

1. A CSV containing exactly 1 data row passes row-count validation.
2. A CSV containing exactly 10,000 data rows passes row-count validation.
3. A CSV containing 10,001 data rows fails file-level validation.
4. A header-only CSV fails file-level validation.
5. A row-limit failure results in zero user mutations.

### FR-007: Header Validation

The server must validate CSV headers before row import.

**Acceptance criteria:**

1. A file missing any required header fails file-level validation.
2. A file containing the same header more than once fails file-level validation.
3. A file containing an unknown header fails under the proposed strict-schema policy.
4. Header matching follows a documented case-sensitivity and whitespace policy. **[OPEN QUESTION]**
5. A header-validation failure results in zero user mutations.

### FR-008: Row Validation

Each data row must be validated against field and business rules before mutation.

**Acceptance criteria:**

1. A row with an invalid email address is rejected with a stable error code identifying `email`.
2. A row missing a required name field is rejected with the applicable field error.
3. A row containing an unsupported role is rejected with a `ROLE_INVALID` error.
4. A row assigning a role beyond the submitter’s authority is rejected with `ROLE_NOT_ASSIGNABLE`.
5. A row exceeding a documented field length is rejected before persistence.
6. A valid row receives no validation errors and becomes eligible for import.

### FR-009: Duplicate Detection Within a File

The system must detect duplicate identities in the same CSV.

**Acceptance criteria:**

1. Given two rows whose normalized emails are equal, when validated, then both rows are associated with a duplicate-email error or handled according to one documented deterministic winner policy.
2. Given two rows with the same non-empty `external_id`, when validated, then the duplicate is reported.
3. Duplicate comparison applies the same normalization rules used by user persistence.
4. Duplicate rows never create more than one user mutation.

### FR-010: Existing-User Conflict Handling

The system must apply a documented policy when a row matches an existing tenant user.

**Proposed behavior:** **[ASSUMPTION]** The initial release creates new users only; an existing email or `external_id` causes the row to be skipped with an error.

**Acceptance criteria:**

1. Given a row matching an existing normalized email, when imported, then no second user is created.
2. The row result contains `USER_ALREADY_EXISTS`.
3. Existing user attributes remain unchanged.
4. Reprocessing the row does not create a duplicate.
5. If create-and-update behavior is later approved, it must be specified as a separate requirement before implementation.

### FR-011: Asynchronous Job Creation

A successful submission must return without waiting for full validation or import completion. **[CONFIRMED MOCK FACT]**

**Acceptance criteria:**

1. Given a valid submission envelope, when accepted, then the API returns `202 Accepted`.
2. The response includes a job identifier, initial state, creation timestamp, and status URL.
3. The response does not imply that row validation or import has completed.
4. The job remains retrievable after the initiating browser session ends.
5. Background processing can start independently of the request connection.

### FR-012: Import Progress

The administrator must be able to view job progress.

**Acceptance criteria:**

1. A non-terminal job response contains its current state.
2. During row processing, the response contains `processed_rows` and `total_rows`.
3. `processed_rows` is never negative, never exceeds `total_rows`, and never decreases.
4. The console refreshes status without requiring a full page reload.
5. Refresh failures display a retryable status message without creating another import.

### FR-013: Partial-Success Processing

**[ASSUMPTION]** After file-level validation succeeds, valid rows must be imported even when other rows are invalid.

**Acceptance criteria:**

1. Given a file with one valid and one invalid row, when processing completes, then the valid row is imported and the invalid row is not.
2. The terminal job state is `COMPLETED_WITH_ERRORS`.
3. `successful_rows` equals 1 and `failed_rows` equals 1.
4. The invalid row appears in the error report.
5. A row-level validation failure does not roll back unrelated successful rows.

### FR-014: Idempotent Submission

Job submission must be idempotent. **[CONFIRMED MOCK FACT]**

**Acceptance criteria:**

1. Given the same tenant, authenticated principal, endpoint, idempotency key, and identical request payload, when submitted more than once within the retention window, then all successful responses reference the same job identifier.
2. Duplicate submissions create at most one processing job.
3. Duplicate submissions create no duplicate user mutations.
4. Given the same idempotency key with a materially different payload, when submitted, then the API returns `409 Conflict`.
5. Idempotency scope and retention duration are documented and enforced.

### FR-015: Idempotent Row Mutation

Each row mutation must be safe to retry after worker interruption.

**Acceptance criteria:**

1. Given a worker interruption after a user is created but before completion is recorded, when the row is retried, then no duplicate user is created.
2. A stable row-operation key links the source job and row number to the mutation result.
3. A repeated row operation returns or reconstructs the original outcome.
4. Concurrent attempts for the same row result in at most one committed mutation.
5. Retrying one row does not alter outcomes already recorded for other rows.

### FR-016: Final Summary

Terminal jobs must display an internally consistent result summary.

**Acceptance criteria:**

1. A terminal summary includes total, successful, failed, and skipped row counts.
2. `successful_rows + failed_rows + skipped_rows = total_rows`.
3. The summary includes creation, processing-start, and completion timestamps when available.
4. A job-level failure includes a non-sensitive failure category and correlation identifier.
5. The console does not display completion before the server reports a terminal state.

### FR-017: Downloadable Error Report

Jobs containing row failures must provide a downloadable error report. **[CONFIRMED MOCK FACT]**

**Acceptance criteria:**

1. Given a completed job with at least one failed or skipped row, when the administrator selects **Download error report**, then a UTF-8 CSV is downloaded.
2. The report includes source row number, stable error code, affected field when applicable, and human-readable remediation text.
3. The report includes enough original row values to correct and retry the row, subject to privacy policy.
4. The report excludes rows that completed successfully unless explicitly documented otherwise.
5. Given a job with no row errors, then no error report is generated or the download action is unavailable.
6. An expired report returns `410 Gone` or another documented expiration response.

### FR-018: Job History

**[ASSUMPTION]** The console should show recent import jobs for the current tenant.

**Acceptance criteria:**

1. The history lists only jobs belonging to the active tenant.
2. Each entry includes job identifier, submitter display name or identifier, filename, state, timestamps, and aggregate counts.
3. Results use deterministic pagination and ordering.
4. Filtering by terminal state returns only matching jobs.
5. Raw file contents are not exposed in list responses.

### FR-019: Cancellation

**[ASSUMPTION]** An administrator may cancel a job before user mutations begin.

**Acceptance criteria:**

1. A job in `QUEUED` or `VALIDATING` accepts a cancellation request.
2. An accepted cancellation eventually reaches `CANCELED`.
3. A canceled job creates zero user mutations.
4. A job in `IMPORTING` or a terminal state rejects cancellation with `409 Conflict`.
5. Repeating a cancellation request is idempotent.

### FR-020: Audit Logging

Security-relevant import activity must be auditable.

**Acceptance criteria:**

1. Job submission records actor, tenant, job identifier, timestamp, source interface, and outcome.
2. Cancellation records actor, job identifier, timestamp, and result.
3. Completion records aggregate counts and terminal state.
4. Error-report download records actor, job identifier, timestamp, and outcome.
5. Audit records do not store raw CSV contents or full error reports.
6. Audit access follows the fictional product’s existing authorization policy. **[ASSUMPTION]**

### FR-021: Retry of System Failures

The processing system must distinguish retryable infrastructure failures from permanent data errors.

**Acceptance criteria:**

1. A documented transient dependency failure triggers bounded automatic retries.
2. A permanent row-validation error is not retried as a system failure.
3. Retry attempts do not violate submission or row-level idempotency.
4. Exhausted retries move the job or row to a documented terminal outcome.
5. Retry count and final failure category are observable to authorized operators.

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
| Field validation | Row | Reject row; continue eligible rows |
| Duplicate in file | Row | Reject according to deterministic policy |
| Existing user | Row | Skip/reject under create-only policy |
| Transient dependency failure | Job or row | Retry with bounded backoff |
| Permanent system failure | Job | Mark `FAILED`; preserve diagnostic metadata |
| Expired artifact | Download | Return `410 Gone` |
| Idempotency conflict | Request | Return `409 Conflict` |

### 11.2 Error Response Shape

```json
{
  "error": {
    "code": "IDEMPOTENCY_KEY_REUSED",
    "message": "The idempotency key was already used with a different request.",
    "correlation_id": "corr_mock_01JMOCK",
    "retryable": false,
    "details": [
      {
        "field": "Idempotency-Key",
        "reason": "PAYLOAD_MISMATCH"
      }
    ]
  }
}
```

All identifiers and values above are synthetic.

### 11.3 Error-Message Requirements

- Messages must state what happened and, when safe, how to correct it.
- Messages must not expose stack traces, internal hostnames, credentials, storage locations, or cross-tenant resource existence.
- Stable machine-readable codes must be separate from localized display text.
- Retry guidance must accurately reflect whether retrying can succeed without changing input.
- Row numbers must refer consistently to physical CSV lines or logical data-row numbers; the selected convention must be documented. **[OPEN QUESTION]**

## 12. Data Model

All entities below are fictional logical models, not claims about an existing database.

### 12.1 `UserImportJob`

| Field | Type | Description |
|---|---|---|
| `job_id` | UUID/string | Globally unique opaque identifier |
| `tenant_id` | String | Tenant ownership boundary |
| `submitted_by` | String | Authorized administrator identifier |
| `original_filename` | String | Sanitized display filename |
| `file_digest` | String | Cryptographic digest for integrity and idempotency checks |
| `schema_version` | String | CSV schema version |
| `state` | Enum | Current job state |
| `total_rows` | Integer | Data-row count |
| `processed_rows` | Integer | Rows reaching a recorded outcome |
| `successful_rows` | Integer | Successful mutations |
| `failed_rows` | Integer | Rejected rows |
| `skipped_rows` | Integer | Rows intentionally not mutated |
| `idempotency_key_hash` | String | Non-reversible representation of the request key |
| `request_fingerprint` | String | Canonical request fingerprint |
| `failure_code` | Nullable string | Job-level failure category |
| `error_report_ref` | Nullable string | Internal artifact reference, not a public URL |
| `created_at` | Timestamp | Creation time |
| `validation_started_at` | Nullable timestamp | Validation start |
| `import_started_at` | Nullable timestamp | Mutation start |
| `completed_at` | Nullable timestamp | Terminal-state time |
| `expires_at` | Timestamp | Metadata or artifact expiry boundary |
| `version` | Integer | Optimistic-concurrency version |

### 12.2 `UserImportRowResult`

| Field | Type | Description |
|---|---|---|
| `job_id` | String | Parent job |
| `row_number` | Integer | Stable source-row reference |
| `row_operation_key` | String | Idempotent mutation key |
| `normalized_identity_hash` | String | Privacy-reduced matching aid |
| `outcome` | Enum | `SUCCEEDED`, `FAILED`, or `SKIPPED` |
| `user_id` | Nullable string | Created user reference |
| `error_codes` | Array | Stable row error codes |
| `error_fields` | Array | Associated CSV fields |
| `attempt_count` | Integer | Processing attempts |
| `created_at` | Timestamp | Result creation |
| `updated_at` | Timestamp | Latest update |

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

## 13. API Contract

The API below is entirely fictional and illustrative.

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
|---|---:|---|
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

### 13.2 Retrieve Job

`GET /mock-api/v1/admin/user-imports/{job_id}`

```json
{
  "job_id": "job_mock_01",
  "state": "IMPORTING",
  "original_filename": "users.csv",
  "total_rows": 10000,
  "processed_rows": 6400,
  "successful_rows": 6350,
  "failed_rows": 50,
  "skipped_rows": 0,
  "created_at": "2026-09-15T03:23:38Z",
  "import_started_at": "2026-09-15T03:24:10Z",
  "completed_at": null,
  "links": {
    "self": "/mock-api/v1/admin/user-imports/job_mock_01"
  }
}
```

### 13.3 List Jobs

`GET /mock-api/v1/admin/user-imports?state=COMPLETED_WITH_ERRORS&cursor=<opaque>&limit=50`

Requirements:

- Default and maximum page sizes must be documented.
- Cursors must be opaque.
- Results must be tenant-scoped.
- Default ordering must be deterministic, proposed as `created_at DESC, job_id DESC`.

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

Proposed response:

```http
HTTP/1.1 202 Accepted
```

```json
{
  "job_id": "job_mock_01",
  "state": "CANCEL_REQUESTED"
}
```

### 13.6 API Semantics

- All timestamps use RFC 3339 UTC.
- All job identifiers are opaque.
- All mutation requests require authentication, authorization, and tenant context.
- The submission endpoint requires an idempotency key.
- The server computes the request fingerprint from canonical metadata and file digest.
- Reusing a key with the same fingerprint returns the original job.
- Reusing a key with a different fingerprint returns `409 Conflict`.
- API versioning must isolate future incompatible schema changes.
- The service must not expose internal storage references in public responses.

## 14. Security and Privacy

### 14.1 Authorization

- The feature must require a dedicated import permission.
- Role assignment must be constrained by the submitting administrator’s authority.
- Job-list, job-detail, cancellation, and report-download endpoints must enforce tenant ownership independently.
- Client-side route protection is not an authorization control.

### 14.2 Tenant Isolation

- Every job and row-result query must include tenant scope.
- Opaque identifiers must not be treated as authorization.
- Cross-tenant requests must not reveal whether a resource exists.
- Background workers must preserve tenant context through every processing stage.

### 14.3 Data Protection

- CSV files and error reports contain personal data and must be encrypted in transit and at rest.
- Temporary artifacts must have time-bounded retention.
- Access to raw files and reports must be limited to explicitly authorized principals and processing components.
- Download responses should use short-lived authorization rather than indefinitely reusable public links.
- Logs must not contain full CSV rows, invitation tokens, or unnecessary personal data.
- Email addresses should be redacted or hashed in operational telemetry where full values are unnecessary.

### 14.4 File Safety

- The server must enforce configured byte-size and row-count limits.
- Parsing must be streaming or otherwise memory-bounded.
- Filenames must be sanitized and never used directly as storage paths.
- CSV formula injection must be mitigated in generated error reports, including values beginning with `=`, `+`, `-`, or `@`.
- Files must not be executed or interpreted as spreadsheet formulas.
- **[ASSUMPTION]** Malware scanning is required before processing, subject to architecture review.

### 14.5 Privacy

- A privacy review must determine the lawful purpose, retention period, access model, and data-subject implications before any real implementation.
- The error report must include only the source fields needed for remediation.
- The product must document whether uploaded data may be used for diagnostics.
- No uploaded data may be used for model training or unrelated analytics without an explicit, separately approved policy.
- **[OPEN QUESTION]** Required retention periods for source files, row results, reports, and audit records remain undecided.

### 14.6 Audit and Abuse Prevention

- Submission, cancellation, completion, and report download must generate audit events.
- Per-tenant and per-principal rate limits must protect the feature from abuse.
- Repeated malformed uploads should be detectable without retaining full file contents in logs.
- Security alerts should distinguish suspected abuse from ordinary data-quality failures.

## 15. Non-Functional Requirements

### NFR-001: Capacity

The system must accept up to 10,000 data rows per import. **[CONFIRMED MOCK FACT]**

**Acceptance criteria:**

1. A 10,000-row valid CSV is accepted without request timeout.
2. Processing remains asynchronous regardless of row count.
3. Peak memory usage remains within a documented limit under the approved deployment profile.

### NFR-002: Performance

**[ASSUMPTION]** Performance objectives:

- Job creation API: p95 under 2 seconds, excluding client upload transfer time.
- Job-status API: p95 under 500 milliseconds.
- A 10,000-row import: 95% complete within 15 minutes under defined nominal load.
- Console progress: no more than 15 seconds behind persisted job state under nominal conditions.

These values require load testing and capacity validation.

### NFR-003: Availability and Durability

**[ASSUMPTION]**

- Submission and status APIs target 99.9% monthly availability.
- Accepted jobs survive individual worker restarts.
- No acknowledged successful row mutation is lost.
- Processing is at-least-once internally, with idempotent effects.

### NFR-004: Scalability

- Work must be partitionable by tenant and job.
- One large job must not indefinitely starve smaller jobs.
- Concurrency limits must protect shared user-management dependencies.
- Per-tenant fairness must be measurable.

### NFR-005: Observability

The system must expose:

- Job counts by state.
- Job and row processing latency.
- Queue age.
- Retry counts.
- File-level and row-level failure categories.
- Idempotency conflicts.
- Error-report generation failures.
- Worker saturation and dependency errors.

Metrics and logs must avoid unnecessary personal data.

### NFR-006: Accessibility

**[ASSUMPTION]** The console must meet WCAG 2.2 Level AA for the import workflow.

**Acceptance criteria:**

1. The workflow is operable by keyboard.
2. Status changes are announced through an appropriate live region without excessive repetition.
3. Errors are associated programmatically with affected controls.
4. Progress is not conveyed by color alone.
5. Download and retry actions have descriptive accessible names.

### NFR-007: Compatibility

**[ASSUMPTION]** The UI supports the fictional product’s current browser-support matrix. Exact browser versions remain unspecified.

### NFR-008: Localization

- API error codes must remain locale-independent.
- Display strings must be externalized.
- CSV headers are English-only in version 1. **[ASSUMPTION]**
- Imported names and departments must preserve valid Unicode.
- Locale-specific templates are out of scope unless separately approved.

### NFR-009: Maintainability

- Parsing, validation, mutation, and reporting must be separable components.
- Validation rules must have automated tests.
- Job-state transitions must be centrally defined and tested.
- API contracts and CSV schema versions must be documented.
- Operational runbooks must cover stuck, failed, and high-volume jobs.

### NFR-010: Recovery

- Retryable failures must use bounded exponential backoff with jitter.
- Jobs stuck beyond a defined threshold must be detectable.
- Operators must be able to resume or safely fail a stuck job without duplicate effects.
- Reconciliation must detect aggregate-count discrepancies.

## 16. Analytics and Success Metrics

All targets below are mock proposals and require baseline validation.

### 16.1 Product Metrics

| Metric | Definition | Proposed target | Status |
|---|---|---:|---|
| Adoption | Tenants using batch import among tenants creating at least 50 users/month | 30% within 90 days | **[ASSUMPTION]** |
| Import completion rate | Jobs reaching `COMPLETED` or `COMPLETED_WITH_ERRORS` divided by accepted jobs | ≥98% | **[ASSUMPTION]** |
| First-pass row success | Successful rows divided by submitted rows on first attempt | ≥90% | **[ASSUMPTION]** |
| Time saved | Estimated administrator time avoided compared with manual creation | Establish through research | **[OPEN QUESTION]** |
| Error remediation | Failed rows successfully imported within seven days | ≥70% | **[ASSUMPTION]** |
| Duplicate-effect incidents | Confirmed duplicate users caused by request or worker retries | 0 | Required |
| Cross-tenant exposure | Confirmed unauthorized cross-tenant access | 0 | Required |

### 16.2 Operational Metrics

- Accepted jobs by tenant and hour.
- Queue age p50/p95/p99.
- End-to-end completion time by row-count band.
- Row throughput.
- File-level failure rate by stable error code.
- Row-level failure rate by field and error code.
- Retry attempts and exhausted retries.
- Job-state age and stuck-job count.
- Error-report generation and download success.
- Idempotency replay and conflict rates.

### 16.3 Analytics Privacy

- Analytics events must not contain raw CSV rows.
- Email addresses, names, and external identifiers must not be analytics dimensions.
- Tenant-level reporting must follow the existing fictional analytics access policy. **[ASSUMPTION]**
- Low-volume dimensions should be aggregated where re-identification risk exists.

### 16.4 Proposed Analytics Events

| Event | Trigger | Allowed properties |
|---|---|---|
| `user_import_page_viewed` | Import page opened | Tenant-safe product context |
| `user_import_template_downloaded` | Template downloaded | Schema version |
| `user_import_submitted` | Job accepted | Job ID, row-count band, schema version |
| `user_import_completed` | Terminal success state | Job ID, duration, aggregate counts |
| `user_import_failed` | Job-level failure | Job ID, failure category |
| `user_import_error_report_downloaded` | Report downloaded | Job ID, error-count band |
| `user_import_canceled` | Job canceled | Job ID, prior state |

## 17. Code Impact

> **SIMULATED PATHS ONLY**
>
> Every path and component in this section is fictional. None is asserted to exist in any repository, filesystem, service, or deployment.

### 17.1 Simulated Frontend Impact

| Simulated path | Simulated change |
|---|---|
| `[SIMULATED] /fictional-saas/admin-console/src/routes/users/import/index.tsx` | Add import route and page orchestration |
| `[SIMULATED] /fictional-saas/admin-console/src/components/user-import/FilePicker.tsx` | Add CSV selection and advisory validation |
| `[SIMULATED] /fictional-saas/admin-console/src/components/user-import/ImportProgress.tsx` | Display job state and progress |
| `[SIMULATED] /fictional-saas/admin-console/src/components/user-import/ImportSummary.tsx` | Display terminal counts and actions |
| `[SIMULATED] /fictional-saas/admin-console/src/api/userImports.ts` | Add typed fictional API client |
| `[SIMULATED] /fictional-saas/admin-console/src/analytics/userImportEvents.ts` | Add privacy-reviewed analytics events |
| `[SIMULATED] /fictional-saas/admin-console/src/i18n/en-US/userImport.json` | Add en-US strings |
| `[SIMULATED] /fictional-saas/admin-console/test/user-import/` | Add component and end-to-end tests |

### 17.2 Simulated Backend Impact

| Simulated path | Simulated change |
|---|---|
| `[SIMULATED] /fictional-saas/services/admin-api/src/routes/user_imports.py` | Add submission, status, list, cancellation, and report routes |
| `[SIMULATED] /fictional-saas/services/admin-api/src/auth/user_import_policy.py` | Add import authorization policy |
| `[SIMULATED] /fictional-saas/services/user-import-worker/src/parser.py` | Add streaming UTF-8 CSV parser |
| `[SIMULATED] /fictional-saas/services/user-import-worker/src/validation.py` | Add file-level and row-level rules |
| `[SIMULATED] /fictional-saas/services/user-import-worker/src/importer.py` | Add idempotent user mutation |
| `[SIMULATED] /fictional-saas/services/user-import-worker/src/error_report.py` | Generate formula-safe CSV error reports |
| `[SIMULATED] /fictional-saas/services/user-import-worker/src/state_machine.py` | Enforce job-state transitions |
| `[SIMULATED] /fictional-saas/services/user-import-worker/test/` | Add parser, validator, retry, and idempotency tests |

### 17.3 Simulated Data and Contract Impact

| Simulated path | Simulated change |
|---|---|
| `[SIMULATED] /fictional-saas/contracts/openapi/admin-user-imports.yaml` | Define fictional REST contract |
| `[SIMULATED] /fictional-saas/contracts/csv/user-import-v1.schema.json` | Define versioned CSV schema |
| `[SIMULATED] /fictional-saas/database/migrations/0000_mock_user_import_jobs.sql` | Add fictional job and idempotency tables |
| `[SIMULATED] /fictional-saas/observability/dashboards/user-import.json` | Add fictional operational dashboard |
| `[SIMULATED] /fictional-saas/runbooks/user-import-processing.md` | Add recovery and support procedures |

### 17.4 Test Impact

The fictional implementation would require:

- Parser tests for quoting, delimiters, line endings, Unicode, malformed rows, and invalid UTF-8.
- Boundary tests for 0, 1, 9,999, 10,000, and 10,001 data rows.
- Header-schema tests.
- Field-validation tests.
- Permission and cross-tenant isolation tests.
- Submission-idempotency tests.
- Row-mutation retry and concurrency tests.
- Partial-success tests.
- Error-report formula-injection tests.
- State-transition tests.
- Cancellation-race tests.
- Load tests using synthetic personal data.
- Accessibility tests.
- Failure-injection and recovery tests.

## 18. Dependencies

All dependencies are fictional categories, not claims about real systems.

| Dependency | Purpose | Status |
|---|---|---|
| Existing administrator authentication | Identify the submitter | **[ASSUMPTION]** |
| Existing role/permission service | Enforce `users.import` and assignable roles | **[ASSUMPTION]** |
| Existing user-management service | Create users and enforce uniqueness | **[ASSUMPTION]** |
| Durable job store | Persist job and row outcomes | Required; implementation unresolved |
| Durable asynchronous work mechanism | Execute validation and import | Required; implementation unresolved |
| Encrypted temporary artifact storage | Hold source files and error reports | Required; implementation unresolved |
| Malware-scanning capability | Inspect uploaded files | **[ASSUMPTION]** |
| Audit subsystem | Record security-relevant actions | **[ASSUMPTION]** |
| Analytics subsystem | Collect privacy-safe product metrics | **[ASSUMPTION]** |
| Notification subsystem | Optional completion notification | **[OPEN QUESTION]** |
| Privacy and security review | Approve data handling and controls | Required before real release |
| Support documentation | Explain schema and remediation | Required before general availability |

No dependency in this table identifies or implies an actual AWS resource.

## 19. Rollout and Rollback

### 19.1 Proposed Rollout

1. **Internal development:** Use synthetic data only; validate parsing, idempotency, isolation, and failure recovery.
2. **Security and privacy review:** Approve threat model, access controls, retention, logging, and report contents.
3. **Internal test tenants:** Enable through a server-side feature flag for authorized test administrators.
4. **Limited preview:** Enable for a small set of fictional opt-in tenants with explicit support coverage.
5. **Expanded availability:** Increase tenant coverage after success and reliability gates pass.
6. **General availability:** Enable by default only after operational readiness and support documentation are complete.

### 19.2 Rollout Gates

- No unresolved critical security or privacy findings.
- Cross-tenant authorization tests pass.
- Submission and row-level idempotency tests pass.
- A 10,000-row load test meets approved performance objectives.
- Duplicate-effect count remains zero.
- Error reports are formula-injection-safe.
- Monitoring and stuck-job alerts are operational.
- Support and rollback runbooks are reviewed.
- Retention and deletion behavior is verified.

### 19.3 Rollback Strategy

- Disable new submissions through a server-side feature flag.
- Preserve status and error-report access for already accepted jobs when safe.
- Stop dequeuing new jobs while allowing in-flight row mutations to reach a consistent boundary.
- Do not delete job, audit, or idempotency records as part of an emergency feature disablement.
- If a mutation defect is detected, suspend processing before attempting remediation.
- Re-enable only after root cause, affected scope, data correction, and retry safety are established.

### 19.4 Rollback Acceptance Criteria

1. Disabling the feature prevents new job creation.
2. Existing terminal jobs remain readable according to retention policy.
3. Rollback does not cause accepted jobs to restart from the beginning without idempotency protection.
4. In-flight jobs reach a documented recoverable state.
5. The system can identify potentially affected users by job and row-operation key.

## 20. Risks and Mitigations

| Risk | Impact | Likelihood | Mitigation |
|---|---|---:|---|
| Duplicate users after retries | High | Medium | Submission and row-level idempotency; uniqueness constraints; concurrency tests |
| Cross-tenant data exposure | Critical | Low | Tenant-scoped authorization on every operation; non-disclosing errors; isolation tests |
| Unauthorized elevated roles | Critical | Medium | Validate assignable roles against submitter authority for every row |
| Malformed or hostile CSV | High | Medium | Streaming parser, byte and row limits, strict schema, safe error reporting |
| CSV formula injection in report | High | Medium | Escape dangerous leading characters; automated tests |
| Large jobs degrade user-management APIs | High | Medium | Concurrency controls, backpressure, per-tenant fairness, load testing |
| Partial success surprises administrators | Medium | Medium | Explain behavior before submission; provide exact summary and error report |
| Files retained too long | High | Medium | Explicit retention policy and automated expiry |
| Support logs expose personal data | High | Medium | Structured error codes, redaction, least-privilege access |
| Worker crash produces inconsistent counts | High | Medium | Durable row outcomes, atomic transitions, reconciliation |
| Existing-user policy is unclear | Medium | High | Resolve create-only versus update semantics before implementation |
| Invitation emails are sent unintentionally | High | Medium | Explicit field/default, preview copy, audit event, rate controls |
| Job cancellation races with import | High | Medium | Restrict cancellation by state; transactional transition checks |
| Schema evolution breaks old templates | Medium | Medium | Versioned schemas and backward-compatibility policy |
| Administrators upload stale HR exports | Medium | Medium | Confirm intent, display filename and row count, preserve audit trail |
| Error report itself becomes a data leak | High | Medium | Authorized download, short retention, no public URLs, download audit |

## 21. Open Questions

| ID | Question | Decision owner | Blocking |
|---|---|---|---:|
| OQ-01 | Is the initial import mode create-only, upsert, or selectable? | Product and Engineering | Yes |
| OQ-02 | Should valid rows import when other rows fail, or must the job be all-or-nothing? | Product | Yes |
| OQ-03 | What is the default and allowed behavior for invitation emails? | Product and Security | Yes |
| OQ-04 | What maximum file byte size complements the 10,000-row limit? | Engineering and Security | Yes |
| OQ-05 | How long are source files, reports, job metadata, row results, and idempotency records retained? | Privacy, Security, and Legal | Yes |
| OQ-06 | Are UTF-8 byte-order marks accepted? | Engineering | No |
| OQ-07 | Are headers case-sensitive, and is surrounding whitespace ignored? | Product and Engineering | Yes |
| OQ-08 | Are unknown columns rejected or ignored? | Product | Yes |
| OQ-09 | How are duplicate rows within one file reported: reject all duplicates or accept a deterministic first row? | Product | Yes |
| OQ-10 | Does `external_id` have tenant-wide uniqueness? | Product and Data | Yes |
| OQ-11 | Are administrators allowed to cancel during `IMPORTING` if no row is currently mutating? | Engineering and Product | No |
| OQ-12 | Should completion notifications be delivered in-console, by email, or not at all? | Product | No |
| OQ-13 | Which roles may view raw uploaded files for support purposes? | Security and Privacy | Yes |
| OQ-14 | Which CSV dialect is authoritative for quoting, line endings, and malformed records? | Engineering | Yes |
| OQ-15 | Does an error report identify physical file lines or logical data-row numbers? | Product and Engineering | No |
| OQ-16 | What browser-support matrix applies? | Product and Frontend Engineering | No |
| OQ-17 | Must CSV headers or error messages support languages beyond en-US at launch? | Product and Localization | No |
| OQ-18 | Is malware scanning required for text-only CSV files under the selected threat model? | Security | Yes |
| OQ-19 | What rate limits apply per administrator and tenant? | Engineering and Operations | Yes |
| OQ-20 | What is the approved service-level objective for 10,000-row completion time? | Product and Operations | Yes |

## 22. Evidence Ledger

This ledger separates supplied mock facts from assumptions and unresolved decisions. It does not reference real customer research, telemetry, repositories, or AWS resources.

| Evidence ID | Statement | Classification | Source | Confidence | Impact |
|---|---|---|---|---|---|
| EL-001 | The feature belongs in an existing SaaS admin console. | Confirmed mock fact | Supplied scenario | High within mock | Defines product surface |
| EL-002 | Administrators are the actors who upload files. | Confirmed mock fact | Supplied scenario | High within mock | Defines primary persona |
| EL-003 | Input files are UTF-8 CSV. | Confirmed mock fact | Supplied scenario | High within mock | Defines parser and validation |
| EL-004 | One file may contain up to 10,000 rows. | Confirmed mock fact | Supplied scenario | High within mock | Defines capacity boundary |
| EL-005 | Validation and import are asynchronous. | Confirmed mock fact | Supplied scenario | High within mock | Requires durable job lifecycle |
| EL-006 | A downloadable error report is required. | Confirmed mock fact | Supplied scenario | High within mock | Requires report generation and protected download |
| EL-007 | XLSX is not supported. | Confirmed mock fact | Supplied scenario | High within mock | Defines non-goal and rejection behavior |
| EL-008 | End-user self-service is not supported. | Confirmed mock fact | Supplied scenario | High within mock | Restricts authorization and UI |
| EL-009 | Idempotency is required. | Confirmed mock fact | Supplied scenario | High within mock | Requires request and mutation safeguards |
| EL-010 | The initial schema includes email, name, role, external ID, department, and invitation behavior. | Assumption | PRD completion assumption | Medium | Must be approved before implementation |
| EL-011 | The initial mode creates users but does not update existing users. | Assumption | Proposed safe default | Medium | Determines conflict handling |
| EL-012 | Valid rows import even when other rows fail. | Assumption | Proposed usability behavior | Medium | Determines transaction model |
| EL-013 | Administrators can cancel before mutation begins. | Assumption | Proposed operational behavior | Medium | Adds state and race complexity |
| EL-014 | Recent import history is visible in the console. | Assumption | Proposed usability behavior | Medium | Adds list API and retention requirements |
| EL-015 | Dedicated `users.import` permission exists or can be introduced. | Assumption | Proposed authorization model | Medium | Requires identity integration |
| EL-016 | Existing user-management validation can be reused. | Assumption | Architectural hypothesis | Low | Must be verified against any real system |
| EL-017 | Source files and reports use encrypted temporary storage. | Assumption/required control | Security design proposal | Medium | Requires approved storage architecture |
| EL-018 | Performance targets in Section 15 are feasible. | Assumption | Unvalidated target proposal | Low | Requires load testing |
| EL-019 | Retention durations are known. | Unresolved question | No evidence supplied | Unknown | Blocks privacy and storage design |
| EL-020 | Invitation behavior is known. | Unresolved question | No evidence supplied | Unknown | Blocks safe user-notification behavior |
| EL-021 | Upsert behavior is desired. | Unresolved question | No evidence supplied | Unknown | Blocks mutation semantics |
| EL-022 | Any simulated code path exists. | Explicitly not claimed | Fictional examples only | None | Must not be treated as repository evidence |
| EL-023 | Any AWS account, service, queue, database, bucket, function, or other resource exists. | Explicitly not claimed | Non-authoritative sample constraint | None | Requires independent discovery in any real project |
| EL-024 | This PRD represents an approved customer or product commitment. | Explicitly not claimed | Non-authoritative sample constraint | None | Must not be used as approval evidence |

## 23. Definition of Ready

This mock feature would be ready for implementation planning only when:

1. All blocking open questions are resolved.
2. CSV schema version 1 is approved.
3. Existing-user and partial-success policies are approved.
4. Invitation behavior is approved.
5. Security, privacy, retention, and tenant-isolation requirements are reviewed.
6. API and state-transition contracts are approved.
7. Capacity and performance objectives are measurable.
8. Simulated code-impact paths are replaced by verified repository evidence, if a real repository is later authorized for inspection.
9. Dependencies are mapped to verified systems rather than assumptions.
10. Acceptance criteria are incorporated into a traceable test plan.

## 24. Definition of Done

A hypothetical implementation would be complete only when:

1. All must-level functional requirements pass automated or documented acceptance testing.
2. Boundary tests cover 0, 1, 9,999, 10,000, and 10,001 rows.
3. Request and row-mutation idempotency are verified under retries and concurrency.
4. Cross-tenant and privilege-escalation tests pass.
5. Error reports are actionable, privacy-reviewed, and formula-injection-safe.
6. A synthetic 10,000-row load test meets approved objectives.
7. Accessibility testing meets the approved standard.
8. Monitoring, alerting, reconciliation, and runbooks are operational.
9. Rollout and rollback procedures are tested.
10. No statement in this mock PRD is treated as evidence that a real repository, implementation, AWS resource, or deployment exists.
