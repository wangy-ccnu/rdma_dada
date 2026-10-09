# MS-03 independent review — 2026-09-30

Coordinator: `01a090fd-55d8-7b83-9ea1-1a53eaa1b952`.
Child: `01a0f2c3-08ab-79b2-897a-091bece9198f`.
Worktree: `/Users/ywang/.codex/worktrees/e338/rdma_dada`.
Base: `5851eaed6cff78179d9cee763c6d4713c1095b08`.
Scope: untracked weights module/tests and fixture diff against the base;
there are no child commits. Authority: `ms03-development-handoff.md`.

## Standards

- P2: post-create write/fsync failure plus unlink failure leaves a complete-looking
  destination, while cleanup exceptions are swallowed. Reproduced with injected
  fsync and unlink failures; an 82-byte NPY remained at the final path. Use owned
  same-directory staging, no-clobber publication after verification, and explicit
  cleanup-failure evidence. Preserve pre-existing/unowned files. Duplicated cleanup
  handlers are a secondary maintainability judgment, not a separate blocker.
- P2: no bounded multi-chunk I/O or post-create write/flush/fsync/close failure
  coverage. These were explicit handoff requirements; tiny happy-path tests do
  not establish the resource/failure contract.

## Spec

- P1: ast.literal_eval loses duplicate dictionary keys. A duplicate shape with
  `(3,1,2,1,2)` followed by `(3,2,1,1,2)` has equal payload size but Python selects
  the latter and the existing C++ readers select the former. Reject duplicates
  before conversion so P/A interpretation cannot silently diverge.
- P2: the slicer rejects NPY v2 with a 96-byte (16-aligned) payload boundary;
  existing readers accept it. Accept historical 16-aligned input; deterministic
  output may remain 64-aligned. Do not change the existing C++ readers.
- P2: missing bounded-I/O/failure tests overlap the Standards finding. Add header
  bounds and boolean/invalid shape cases as well as source aliases and output
  ownership regressions.

Standards: 2 findings; worst P2. Spec: 3 findings; worst P1. Counts overlap.
No scope creep found. Version minor-byte policy was not made an extra blocker:
the current C++ readers also select the format from the major byte.

## Verification and disposition

Coordinator independently ran:

```text
python3 -B -m unittest discover -s tests -p 'multiserver_*test.py' -q
Ran 68 tests ... OK (exit 0)
git diff --check  # exit 0
```

Two independent reviewers reproduced the relevant parser/failure cases.
This is local development evidence, not server acceptance. Status is
CHANGES_REQUIRED; the correction request was delivered to the same Luna task.
No manifest refresh during corrections, remote test, commit or push.

## Correction re-review — 2026-10-08

The coordinator recovered the child report by read_thread after its callback
was refused; notification remains FAIL, not a successful callback. Independently
rerun combined regression: 78 tests PASS. Duplicate-key handling and historical
NPY v2 alignment are corrected. Output write/flush/fsync/close failures now use
owned temporary storage and no-clobber publication.

Spec re-review retains two P2 findings:

- Publication precedes temporary cleanup and source close. Injected unlink or
  source-close failures raise an exception with a final destination still
  present. Close source before publication and make post-publication cleanup/
  rollback state explicit; preserve foreign files and original error evidence.
- The chunk test changes the chunk constant and checks bytes, but never asserts
  read/write request sizes. Add guarded streams proving bounded, multi-chunk
  payload I/O. Current implementation is bounded; the regression is inadequate.

The correction was delivered to the same Luna task. No server acceptance is
claimed. Standards re-review is pending, separate from the Spec findings.

### ponytail-review (advisory, not applied)

1. `scripts/multiserver/weights.py:L139`: shrink: three repeated source-close exception handlers; consolidate shared close cleanup while retaining error classification. Approximately 6 lines removable.

net: -6 lines possible.

This complexity-only suggestion does not replace correctness review and does
not authorize unrelated refactoring. Re-evaluate after the failure-path fix.

## Final local re-review — 2026-10-08

Second-round correction recovered through wait_threads; child notification
remained FAIL. Source close now precedes publication. Post-publication cleanup
failure attempts ownership-checked rollback and explicitly identifies retained
temporary/final paths when removal is impossible. Guarded I/O tests check request
sizes and multiple chunks. Both previously outstanding Spec findings resolved.

Standards: 0 blocking findings, 1 optional duplicate-cleanup advisory (above).
Spec: 0 remaining findings. Coordinator and independent reviewers verified
21 weight tests / 81 combined tests PASS, and git diff --check PASS.

Reviewed snapshot: weights.py SHA256
`b02f1e0f9813c6f1b1ba007f0d4406e8631efc7bf935cb47335d2838d424ba24`;
weights test SHA256
`8938621cf3e3da5c6461c31bbf23de32f34f186007a7452e3767f81049eb8608`.
Source remains in the child checkout. Local review PASS; server acceptance
pending the combined configuration-stage handoff. No commit/push/archive.
