# MS-05 coordinator review — 2026-10-08

Final status: ACCEPTED_OFFLINE_CONFIGURATION; see `ms03-ms05-acceptance.md`.
Earlier pending/correction states below document the review sequence.

Task `MS-05 现有编译器兼容性验收`, ID
`01a11b7e-d6d1-7942-b378-5e520e48d2c3`; coordinator
`01a090fd-55d8-7b83-9ea1-1a53eaa1b952`.
Implementation `/Users/ywang/.codex/worktrees/179b/rdma_dada`, base5851eaed.

Initial delivery was retrieved through read_thread after callback refusal.
Coordinator independently reran the eleven registered multiserver/observation/
artifact/weight CTests:11/11 PASS. First attempt could not write CTest logs in
the child worktree under the coordinator sandbox; the same command with approved
filesystem escalation passed. This was local test-log access, not product error.

## Reproduced correction request

P2, compiler.py post-publication cleanup: `_publish_exclusive` succeeds, then
the final `workspace.rmdir()` raises an uncaught PermissionError. Coordinator
ran the actual compiler with only that final Path.rmdir failure injected:
exception_type=PermissionError, has_details=false, output_published=true,
published resolution.json ok=true. CLI would emit a traceback rather than a
structured publication/cleanup outcome.

Same child was asked to retain successful artifacts, explicitly distinguish
publication from cleanup, persist accurate diagnostics and add a real-compiler
fault-injection regression. No request to modify the existing C++ compiler or
algorithms. Review remains CHANGES_REQUIRED pending verification of the fix.

## Existing compatibility boundary

`src/config/resolved_observation_plan.cpp:138` rejects weight dtype that differs
from antenna input width. The current wire is CI8. NPY v1/v2 int8 resolves;
int16 slicing/metadata parsing remains supported, but full CI8 resolution must
reject it without conversion. Coordinator approved explicit negative coverage
and documentation, not a false claim of full int16 compatibility.

Initial relevant tests passed locally. The child's extra portable run was53/54;
the unchanged loopback test was denied localhost binding by its sandbox. That
environment result is outside the MS-05 compatibility acceptance and is not a
new product failure. Server acceptance and source integration remain pending.

## Correction acceptance

LOCAL_REVIEW_PASS / SERVER_TEST_PENDING. The same child fixed publication versus
cleanup reporting: successful publication survives cleanup errors with a persisted
warning; failure to persist that warning raises a structured error with
`published=true`, never a fictitious rollback. Coordinator reviewed the final
diff and independently verified 133 pure tests, 12 real-compiler integration
tests and all 11 targeted registered CTests. Discovery reports145 tests with12
integration skips; those12 were separately executed against the actual compiler.
Diff check passed. Independent Spec review passed; Standards found no additional
violations in the preceding delivery, and coordinator reviewed the cleanup delta.
Ponytail review found no additional actionable complexity cuts.

Stage-frozen source is retained in the child worktree. Its generated
`config/testing/multiserver-source-manifest.sha256` has312 entries, locally
verified, file SHA256
`fa0483687947d874146dc276afe69eadf4d2ec241b4cd372ca90a27d09ca7553`.
No algorithms, C++ compiler contracts, paper branch, commit or push were changed.
