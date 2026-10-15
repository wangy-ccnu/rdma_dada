# Delegated development and acceptance

Status: user-approved working rule, recorded 2026-09-13. Applies to future repository development tasks; it does not retroactively change the model or ownership of existing paper/test tasks.

## Roles and authority

- The coordinating task owns scope, task splitting, dependencies, assignment, independent review and acceptance. The coordinator is the user's primary contact and keeps the overall plan current.
- User update on 2026-10-08: new bounded implementation tasks use separate Codex conversations with model `gpt-6.1-sol` and reasoning `high`. This supersedes the original Luna default. Already-started MS-03/MS-04 retain their recorded model unless the user requests a change. Use the app's task creation interface, not an ephemeral sub-agent as a substitute for a persistent conversation. If the requested model/effort is unavailable, report it and request a model decision; do not silently substitute.
- The existing `GPU服务器代码测试` task retains synchronization, remote build/test and result-reporting ownership. Implementation tasks do not replace that workflow or run remote tests themselves.
- The user retains commit, push, merge and material scope-change decisions. Passing review or tests is not permission for these actions.
- This rule permits future approved work packages to be assigned to new persistent conversations; merely saving the rule does not authorize starting all roadmap tasks or unrelated work.

## 1. Split and prepare

### Markdown necessity rule

User-confirmed 2026-10-11: before creating or retaining a Markdown document, ask:

1. Does the user need to read it?
2. Does subsequent development or testing need to use it?

At least one answer must have a concrete purpose and reader; otherwise do not
create a permanent document. Prefer updating the existing authoritative document
and linking to it over duplicating content or creating another status/report file.
Active handoff/review notes may serve ongoing work. At stage closure, consolidate
their unique contracts, unresolved items, reproduction commands and evidence links
into maintained guides or acceptance records; remove redundant process documents
within the authorized cleanup scope. Check references before deletion and preserve
original test evidence and frozen identities. Merely having generated a document
is not a reason to keep it or move it into a growing archive directory.

Assign one bounded deliverable with independently checkable acceptance criteria, not an entire subsystem with vague completion. Dispatch only tasks whose dependencies are available. Avoid concurrent changes to the same files; shared model/schema/CMake edits are serialized or owned by one named task.

Every handoff must include:

1. Coordinator task ID, plan/spec paths and exact task objective.
2. Explicit base branch/revision, source worktree and destination checkout; existing changes and ownership boundaries.
3. Required files/interfaces, permitted edits and out-of-scope behavior.
4. Input fixtures, exact local test commands, expected positive/negative behavior and required server gates.
5. Completion/reporting contract, including where the coordinator can read the diff, tests and evidence.
6. No automatic commit/push/merge, no paper-branch modification, and the conditions for stopping to ask a question.

Inspect project metadata before task creation and explicitly start from the approved feature branch/state. Do not let a default branch or older remote ref silently replace the handed-off base. The coordinator must resolve uncommitted dependencies before dispatch; a fresh worktree does not inherit another task's dirty edits automatically.

For multi-server work, use `codex/multi-server-observation` and isolated task worktrees derived from the approved feature state. Preserve `codex/pipeline-architecture`, its dirty paper work and existing ablation branches. Use the isolation policy in `docs/superpowers/plans/2026-09-12-multi-server-control-roadmap.md`. New task creation is not permission to merge between these branches.

Record task title/ID, model, branch/worktree, base identity, dependency, file ownership, scope and status in a feature-local ledger. For this feature the ledger path is `docs/control/task-ledger.md`; the first actual dispatch creates it. Do not create empty pretend tasks or record a task as dispatched until the creation tool succeeds. Use the returned title verbatim.

## 2. Implementation and delivery

The implementation task reads the supplied repository rules/spec/plan, uses the agreed test-driven workflow, implements only its scope and reports blockers instead of inventing paths, changing the contract or bypassing required gates.

At handoff back, call `send_message_to_thread` with the coordinating task ID. Verify delivery succeeded; on failure resolve the ID and retry once, then explicitly report the notification failure. A final message only inside the child conversation is not a confirmed handoff.

The completion report contains:

```text
TASK_ID / TASK_TITLE:
MODEL / BASE_REVISION / BRANCH / WORKTREE:
IMPLEMENTATION_RESULT: COMPLETE | PARTIAL | BLOCKED
CHANGED_FILES_AND_BEHAVIOR:
TEST_COMMANDS_AND_RESULTS:
EVIDENCE_PATHS_AND_IDENTITIES:
KNOWN_LIMITATIONS_OR_DEVIATIONS:
SERVER_TEST_REQUIREMENTS:
COMMIT_PUSH_MERGE: NOT_PERFORMED (unless separately authorized)
RESULT_NOTIFICATION: PASS | FAIL
ARCHIVE_STATUS: KEEP_OPEN_PENDING_ACCEPTANCE
```

Self-tests establish development evidence, not coordinator acceptance or server acceptance. A task must not call itself accepted because its own tests passed.

### Source identity checkpoints

User-approved on 2026-09-30: refresh source manifests and recorded SHA256 values
at stage-completion freezes and formal test handoffs/acceptance, not after every
edit, review correction or local development test. Use Git diffs during ongoing
development; an earlier manifest describes its frozen snapshot, not the dirty
working tree, and does not block continued local work. Before formal testing,
freeze the intended inputs, refresh their identity once, and verify synchronized
sources against it. Unchanged retries reuse that identity; changed test inputs
require a new test snapshot. Preserve historical test hashes and results rather
than rewriting them. Routine reports use branch/short commit/status; keep full
hashes in the evidence and handoff records.

## 3. Coordinator acceptance

The coordinator waits on the actual task ID/cursor, reviews the delivered diff against scope and reads executable test evidence. Completion notifications and timeouts are not substitutes for review. Check new failure paths, compatibility, resource ownership and whether expected files/identities match the delivered implementation.

Keep the coordinator turn active through an approved handoff and its acceptance
work. When a child completes, retrieve its delivery and start review without
requiring another user prompt. If callback delivery fails, use the task's actual
ID with `wait_threads`/`read_thread`, record the notification failure separately,
and continue review from the retained diff and evidence. Child freezing prevents
concurrent edits; it does not pause coordinator acceptance. Pause only for an
explicit user stop, an evidenced external blocker, or a decision requiring new
authority. When all children are idle, reconcile each ledger state against its
latest delivery before reporting the next step.

Return findings to the same implementation conversation for correction. Re-review corrected behavior and verify relevant tests; do not open duplicate implementation tasks simply because a fix is required.

For every completed feature/behavior change, the coordinator ensures the required handoff reaches `GPU服务器代码测试`. The implementation task supplies the affected paths/fixtures; the test task performs the actual source synchronization and remote gates. Confirm that returned evidence tests the handed-off code identity, not a different or older checkout. Apply the existing repository failure, cleanup and notification rules unchanged.

Use distinct ledger states:

```text
PLANNED -> RUNNING -> REVIEW_PENDING
                    -> CHANGES_REQUIRED -> REVIEW_PENDING
REVIEW_PENDING -> SERVER_TEST_PENDING -> ACCEPTED
                                   -> CHANGES_REQUIRED
ACCEPTED -> USER_REVIEW_PENDING (when required) -> CLOSED/ARCHIVED or KEEP_OPEN
```

BLOCKED is possible at any stage and names its prerequisite. Documentation-only work may be accepted after document validation without pretending a server feature test occurred. Human sign-off, when required, is part of closure: do not archive while it is outstanding.

Before acceptance, the coordinator must be able to locate the accepted changes and evidence outside reliance on the child's transient context. If the code lives in a child worktree, record and retain that worktree and its identity; do not silently copy over dirty parent files or commit to make integration convenient. Summarize accepted work to the user and remind them when it is ready for a commit; the user decides.

## 4. When to involve the user

Ask for user review when a decision changes agreed scope, algorithms or public contracts; when a visible Web/control workflow needs usability acceptance; when safety/resource permissions or destructive behavior exceed existing authorization; or when unresolved trade-offs cannot be settled by the approved spec and evidence.

Routine code review, reproducible tests and contained fixes remain the coordinator's responsibility. Do not pass factual investigation to the user or demand repeated approvals for an already-approved bounded task.

## 5. Child-task archive decision

After the coordinator explicitly confirms acceptance and required human sign-off is complete, send that result to the child conversation. The child then decides whether it can archive itself and reports its reason/status.

Trigger this archive check in the same coordination flow as acceptance; do not
wait for the user to remind you. Evaluate the child's bounded deliverable, not
completion of the entire project. Later integration/testing may transfer explicitly
to the coordinator and server-test owner without being labelled completed.
"May need follow-up later" alone is not a reason to keep an accepted child open:
record the follow-up owner and retained evidence, then archive the conversation.
KEEP_OPEN must name a concrete task still assigned to that child.

Archive only when all of the following hold:

- The bounded task is accepted, with no pending correction, remote test, human review or active operation.
- Implementation location, source identity, limitations and evidence are retained and recorded in the coordinator's ledger.
- The completion report was successfully delivered and no unique unresolved context remains only in the child's conversation.
- No required follow-up depends on keeping this task active. Reusable investigation or an imminent correction may justify keeping it open.

If eligible, the child calls the app archive operation for its own task, verifies success and reports the archive decision to the coordinator (or sends the pre-archive completion notice and lets the coordinator verify the tool outcome). The coordinator records ARCHIVED only after confirmed success. If an archive call fails, retain KEEP_OPEN/ARCHIVE_FAILED rather than claiming it happened.

Archive changes conversation visibility only. It does not authorize deleting worktrees, branches, source files, logs, evidence, remote resources or Git history. Preserve anything needed for paper supplementary tests or reproduction. Never archive the coordinating task, paper-writing task or shared GPU-server testing task under this child-task rule.

## 6. Persistence and scope

The local repository AGENTS entry activates this workflow for new tasks. This file is the versionable detailed procedure on the feature branch. Include its exact accessible path in each new task handoff; if the destination starts from a revision without this uncommitted document, provide the full rule in the prompt or point to the retained authoritative file, rather than assuming it was copied.

No workflow rule can override the actual tool/model availability or permissions. Report unavailable capabilities honestly; do not bypass safety gates or silently replace persistent tasks with an unrelated mechanism.
