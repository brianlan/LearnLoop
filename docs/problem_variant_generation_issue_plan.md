# Problem Variant Generation — Issue Plan

Status: confirmed planning artifact for the seven-issue variant-ingestion backlog (#609–#615). Documentation only; no production behavior is implemented by this file.

## Decision precedence

1. Latest explicit user-confirmed decisions (recorded below and in [#609](https://github.com/brianlan/LearnLoop/issues/609)) take precedence over everything else.
2. [docs/problem_variant_generation_requirement_document.md](problem_variant_generation_requirement_document.md) is the original product requirements, retained for traceability.
3. [docs/problem_variant_generation_implementation_proposal.md](problem_variant_generation_implementation_proposal.md) is the confirmed implementation proposal.
4. `docs/problem_variant_generation_technical_design_document_v2.md` is reference material only, **not** the implementation contract: it drops required permanent approval provenance, assumes unsafe answer normalization, and misses concurrency and text-only grading constraints.

## Confirmed amendments (supersede conflicting details)

- Independent validators first solve both problems without expected answers, images, generator reasoning, or other validators' reports. The existing helper VLM subsequently compares each validator's two answer pairs; multiple questions/blanks and different equivalent forms are supported. No strict-normalization fallback.
- Difficulty and numeric complexity each have exactly three categories: comparable, materially-easier, materially-harder. Slightly easier/same/slightly harder are one comparable category.
- Candidate problemType must equal the confirmed source type; mismatch is FAIL with evidence, no silent coercion or automatic regeneration.
- After submission, normal and variant Problems edit the same top-level text/problemType/graphDsl/correctAnswer/tags fields, with no revalidation. The entire Problem.variation record remains unchanged, including original and acceptedVariant answer snapshots.
- PASS is an admission gate for the current pre-submit revision, not a claim about later edited Problem content.

### Superseded difficulty wording

The original requirement document (§12.3, §12.4) describes difficulty with a five-level scale (much easier / slightly easier / approximately the same / slightly harder / much harder). That wording is **superseded** by the confirmed three-category contract above: slightly easier, approximately the same, and slightly harder collapse into `comparable`. The five-level text is retained in the source snapshot for traceability and must not be read as the validation contract; the confirmed proposal (§6) records the same collapse.

## Issue map

| Issue | Title | Area | Status |
|---|---|---|---|
| [#609](https://github.com/brianlan/LearnLoop/issues/609) | Document the confirmed variant ingestion contract and requirement coverage | docs | this issue |
| [#610](https://github.com/brianlan/LearnLoop/issues/610) | Make bulk ingestion item mutations atomic before background variant generation | backend | planned |
| [#611](https://github.com/brianlan/LearnLoop/issues/611) | Support text and GraphDSL context in practice and exam short-answer grading | backend | planned |
| [#612](https://github.com/brianlan/LearnLoop/issues/612) | Implement math variant generation and independent validation with helper answer comparison | backend | planned |
| [#613](https://github.com/brianlan/LearnLoop/issues/613) | Add durable per-item variant generation, edit invalidation, and background recovery | backend | planned |
| [#614](https://github.com/brianlan/LearnLoop/issues/614) | Save validated variants transactionally with immutable provenance and audit images | backend | planned |
| [#615](https://github.com/brianlan/LearnLoop/issues/615) | Add the variant ingestion review workflow and immutable provenance display | frontend, e2e | planned |

## Dependency graph

```text
#609 (this issue: docs + coverage map)
  ├─ #610  atomic item mutations     ┐
  ├─ #611  text/GraphDSL grading     ├ parallel prerequisites
  └─ #612  generation + validation   ┘

#613  depends on #610, #612
#614  depends on #611, #613
#615  depends on #613, #614
```

No cycles exist. #609 blocks #610, #611, and #612.

## Permanent vs temporary state

- The temporary ingestion-item workflow state — status transitions, `contentRevision`, `claimToken`/`leaseUntil`, `validatedRevision` — is specified in the implementation proposal §4–§5 and lives on the ingestion batch item, not on the permanent Problem.
- The permanent `Problem.variation` sub schema — mode, confirmed original with audit image, accepted variant snapshot, generator identity, generation count, successful validation reports — is specified in the implementation proposal §9.1. It is written once at submission and never updated by later edits.
- The two must not be conflated: worker ownership/revision fields never enter `Problem.variation`, and `Problem.variation` never participates in ingestion-item writes.

## Planned vs current source files

Current at the time of this plan (all verified to exist on master):

- `backend/app/infrastructure/ingestion/repository.py` — ingestion repository whose multi-path whole-`items` writes #610 must make atomic
- `backend/tests/integration/test_ingestion_atomicity.py` — existing lost-update reproduction scenarios
- `backend/app/presentation/problem_creation.py` — Problem creation from draft
- `backend/app/presentation/exams.py` — existing exam-submission transaction pattern
- `backend/app/infrastructure/vlm/client.py` — VLM request transport (image-required base; text requests must not inherit that invariant)
- `backend/app/domain/normalization.py` — existing answer normalization (must not become the validation answer gate)
- `frontend/src/components/BulkReviewStep.tsx` — frontend bulk-review autosave
- `backend/docs/ingestion_repository_mutation_catalog.md` — mutation catalog of the paths above

Planned until their implementing issue lands (do not assume they exist):

- `app/problem_variation.py` — variant workflow module (#612/#613)
- Variant generator/validator prompts and helper answer-equivalence operation (#612)
- Permanent `Problem.variation` persistence and audit-image copy (#614)
- Review workflow UI and provenance display (#615)

## Related completed research

- [#452](https://github.com/brianlan/LearnLoop/issues/452) — Investigate atomic update migration for ingestion repository (closed). Evidence for the repository mutation catalog and the atomicity constraints behind `LIFE-concurrent-state-safety`. Research only; the production change is implemented by #610.
- [#508](https://github.com/brianlan/LearnLoop/issues/508) — Build a real-Mongo ingestion concurrency characterization harness (closed). Evidence for concurrent-write behavior. Research only; it implements no requirement by itself.

---

## Requirement Traceability

Source: docs/problem_variant_generation_requirement_document.md plus the confirmed decisions embedded in #609 and docs/problem_variant_generation_implementation_proposal.md. These are inferred stable IDs; source section numbers are references, not IDs.

- `OUT-equivalent-practice` — §1–2, §21: 产生同知识、同解法、相近难度的新练习题.
- `FR-ingestion-modes` — §3–4: 创建 batch 前选择三种模式，batch 内锁定.
- `COMP-original-ingestion` — §3.1, §22: 缺省 Original，已有原题流程兼容.
- `FR-data-only-mode` — §3.2: 仅改变数学数据，保留措辞、角色与所求.
- `FR-data-wording-mode` — §3.3: 可改变数据和表面语境，保持数学结构.
- `FR-source-preparation` — §5: 校对题干、题型、GraphDSL、答案并显示 crop.
- `FR-confirmed-answer` — §5, §22: 生成前必须提供答案，Generate 即确认.
- `DATA-generation-source-snapshot` — §5: 每次生成锁定用户当前确认的原题.
- `FR-source-edit-invalidates` — §5, §22: 原题语义修改使旧候选与结果失效.
- `FR-per-problem-background` — §6, §15: 逐题触发并持续后台处理，可换题和刷新.
- `FR-complete-candidate` — §7: 候选包含完整题干、题型、答案和所需 GraphDSL，实际改变数据.
- `DATA-source-identity` — §7, Q3: 保持 subject/领域，题型不一致 FAIL 并显示证据.
- `FR-core-knowledge` — §7.1, §12.1: 保持核心知识.
- `FR-solution-structure` — §7.1, §12.2: 保持推理结构、方向、方法和量的角色.
- `FR-comparable-difficulty` — §7.1, §12.3, Q2: 三类难度，只 comparable 通过.
- `FR-comparable-numeric` — §7.1, §12.4, Q2: 独立评估数值复杂度，重大增减失败.
- `FR-skill-preservation` — §8, §12.5: 禁止实质表示变化或新技能.
- `FR-graph-consistency` — §9, §12.7: 图文与数据一致，安全语法不替代数学判断.
- `FR-independent-validator-config` — §10, §13: 显式配置一个或两个独立验证者.
- `FR-blind-solving-pair` — §10–11: 验证者独立解两题，不接收预期答案、原图或其他报告.
- `FR-helper-answer-equivalence` — §11, Q1: helper 比较每个验证者两对答案，支持多问多空和不同格式.
- `FR-category-agreement` — §13.2, Q2: 关键 category 冲突失败，解释文字差异不算冲突.
- `FR-well-posed-problems` — §7.1, §12.8: 原题与候选可解、条件充分且无歧义.
- `FR-pass-only-review` — §4, §13, §16: 只有当前版本 PASS 可终审和提交.
- `FR-failure-evidence` — §14, Q3: 区分模型执行失败和内容失败，显示可读具体依据.
- `FR-manual-regeneration` — §14, §22: 失败用户手动 Generate Again，全流程重跑，无固定次数上限.
- `FR-no-fallback-override` — §13–14, §22: 禁止失败回退原题、强行接受与自动重生成.
- `FR-pre-submit-review` — §16: 终审主要显示候选，原 crop 可参考.
- `FR-candidate-edit-revalidation` — §16: 入库前候选语义修改后验证，不重新生成.
- `FR-tags-no-revalidation` — §16: 标签是共享元数据，不影响验证.
- `DATA-single-saved-variant` — §17, §22: 1→1 保存变式，原题不作为练习题保存.
- `DATA-source-audit` — §18: 永久确认原题、答案、GraphDSL 和原 crop，可查看.
- `SEC-audit-context-isolation` — §18, §24: 原图不进入练习、考试、Solution、Coaching 或变式模型上下文.
- `DATA-approval-provenance` — §19: 保存 mode、模型、helper、成功报告、PASS、生成次数.
- `DATA-immutable-approval-snapshot` — 后续 schema/不可变确认: 保存 acceptedVariant 快照，入库后整个 variation 不变.
- `FR-shared-post-submit-editing` — Q4 与后续确认: 两类题共用主字段编辑，不再验证.
- `SCOPE-math-only` — §20: 仅实现数学规则，不加学科识别或假想策略框架.
- `LIFE-worker-recovery` — §15, 实施方案 §5: 持久化候选、lease/token、崩溃恢复、过期停止.
- `LIFE-concurrent-state-safety` — §5/15/16, 实施方案 §2/4: 跨题写入不覆盖，版本与删除、旧 worker 有一致性保护.
- `LIFE-submit-transaction-idempotency` — §17–19, 实施方案 §9: PASS 再核实、创建、task 与提交记录事务一致且并发幂等.
- `LIFE-audit-retention-cleanup` — §18, 实施方案 §9: 永久副本经临时批次清理仍存在，未引用副本可安全清理.
- `OPS-model-profiles-explicit-errors` — §10/14/15, 实施方案 §7: 明确角色配置、有限传输重试、错误分类和调用身份.
- `FR-downstream-grading-text-graph` — §2/18/24, 代码衍生约束: 纯文本与 GraphDSL 简答在练习/考试可判分.
- `NFR-quality-verification` — §21/24, 实施方案 §11: 程序 gate 回归与真实模型样本核验分别证明.

### Requirement Inventory

| Requirement ID | Source | Required behavior | Implementing issues |
|---|---|---|---|
| `OUT-equivalent-practice` | §1–2, §21 | 产生同知识、同解法、相近难度的新练习题 | #609, #612, #615 |
| `FR-ingestion-modes` | §3–4 | 创建 batch 前选择三种模式，batch 内锁定 | #613, #615 |
| `COMP-original-ingestion` | §3.1, §22 | 缺省 Original，已有原题流程兼容 | #610, #611, #613, #614, #615 |
| `FR-data-only-mode` | §3.2 | 仅改变数学数据，保留措辞、角色与所求 | #612, #615 |
| `FR-data-wording-mode` | §3.3 | 可改变数据和表面语境，保持数学结构 | #612, #615 |
| `FR-source-preparation` | §5 | 校对题干、题型、GraphDSL、答案并显示 crop | #613, #615 |
| `FR-confirmed-answer` | §5, §22 | 生成前必须提供答案，Generate 即确认 | #613, #615 |
| `DATA-generation-source-snapshot` | §5 | 每次生成锁定用户当前确认的原题 | #613, #614 |
| `FR-source-edit-invalidates` | §5, §22 | 原题语义修改使旧候选与结果失效 | #613, #615 |
| `FR-per-problem-background` | §6, §15 | 逐题触发并持续后台处理，可换题和刷新 | #613, #615 |
| `FR-complete-candidate` | §7 | 候选包含完整题干、题型、答案和所需 GraphDSL，实际改变数据 | #612, #613 |
| `DATA-source-identity` | §7, Q3 | 保持 subject/领域，题型不一致 FAIL 并显示证据 | #612, #613, #615 |
| `FR-core-knowledge` | §7.1, §12.1 | 保持核心知识 | #612 |
| `FR-solution-structure` | §7.1, §12.2 | 保持推理结构、方向、方法和量的角色 | #612 |
| `FR-comparable-difficulty` | §7.1, §12.3, Q2 | 三类难度，只 comparable 通过 | #612 |
| `FR-comparable-numeric` | §7.1, §12.4, Q2 | 独立评估数值复杂度，重大增减失败 | #612 |
| `FR-skill-preservation` | §8, §12.5 | 禁止实质表示变化或新技能 | #612 |
| `FR-graph-consistency` | §9, §12.7 | 图文与数据一致，安全语法不替代数学判断 | #612, #615 |
| `FR-independent-validator-config` | §10, §13 | 显式配置一个或两个独立验证者 | #612, #613 |
| `FR-blind-solving-pair` | §10–11 | 验证者独立解两题，不接收预期答案、原图或其他报告 | #612, #613 |
| `FR-helper-answer-equivalence` | §11, Q1 | helper 比较每个验证者两对答案，支持多问多空和不同格式 | #612, #613 |
| `FR-category-agreement` | §13.2, Q2 | 关键 category 冲突失败，解释文字差异不算冲突 | #612, #615 |
| `FR-well-posed-problems` | §7.1, §12.8 | 原题与候选可解、条件充分且无歧义 | #612 |
| `FR-pass-only-review` | §4, §13, §16 | 只有当前版本 PASS 可终审和提交 | #612, #613, #614, #615 |
| `FR-failure-evidence` | §14, Q3 | 区分模型执行失败和内容失败，显示可读具体依据 | #612, #613, #615 |
| `FR-manual-regeneration` | §14, §22 | 失败用户手动 Generate Again，全流程重跑，无固定次数上限 | #613, #615 |
| `FR-no-fallback-override` | §13–14, §22 | 禁止失败回退原题、强行接受与自动重生成 | #612, #613, #614, #615 |
| `FR-pre-submit-review` | §16 | 终审主要显示候选，原 crop 可参考 | #615 |
| `FR-candidate-edit-revalidation` | §16 | 入库前候选语义修改后验证，不重新生成 | #613, #615 |
| `FR-tags-no-revalidation` | §16 | 标签是共享元数据，不影响验证 | #613, #614, #615 |
| `DATA-single-saved-variant` | §17, §22 | 1→1 保存变式，原题不作为练习题保存 | #614, #615 |
| `DATA-source-audit` | §18 | 永久确认原题、答案、GraphDSL 和原 crop，可查看 | #614, #615 |
| `SEC-audit-context-isolation` | §18, §24 | 原图不进入练习、考试、Solution、Coaching 或变式模型上下文 | #611, #612, #614, #615 |
| `DATA-approval-provenance` | §19 | 保存 mode、模型、helper、成功报告、PASS、生成次数 | #612, #613, #614 |
| `DATA-immutable-approval-snapshot` | 后续 schema/不可变确认 | 保存 acceptedVariant 快照，入库后整个 variation 不变 | #614, #615 |
| `FR-shared-post-submit-editing` | Q4 与后续确认 | 两类题共用主字段编辑，不再验证 | #614, #615 |
| `SCOPE-math-only` | §20 | 仅实现数学规则，不加学科识别或假想策略框架 | #612, #613, #615 |
| `LIFE-worker-recovery` | §15, 实施方案 §5 | 持久化候选、lease/token、崩溃恢复、过期停止 | #613 |
| `LIFE-concurrent-state-safety` | §5/15/16, 实施方案 §2/4 | 跨题写入不覆盖，版本与删除、旧 worker 有一致性保护 | #610, #613, #615 |
| `LIFE-submit-transaction-idempotency` | §17–19, 实施方案 §9 | PASS 再核实、创建、task 与提交记录事务一致且并发幂等 | #614 |
| `LIFE-audit-retention-cleanup` | §18, 实施方案 §9 | 永久副本经临时批次清理仍存在，未引用副本可安全清理 | #614 |
| `OPS-model-profiles-explicit-errors` | §10/14/15, 实施方案 §7 | 明确角色配置、有限传输重试、错误分类和调用身份 | #612, #613 |
| `FR-downstream-grading-text-graph` | §2/18/24, 代码衍生约束 | 纯文本与 GraphDSL 简答在练习/考试可判分 | #611, #615 |
| `NFR-quality-verification` | §21/24, 实施方案 §11 | 程序 gate 回归与真实模型样本核验分别证明 | #612, #615 |

### Non-goals

- `NG-multiple-variants`: 不生成一源多题.
- `NG-save-original-practice`: 不将原题另存为练习题.
- `NG-generate-all`: 不增加 Generate All.
- `NG-fallback-override`: 不回退原题或接受失败.
- `NG-side-by-side`: 不要求原题/候选并排视图.
- `NG-generation-limit`: 不设用户重新生成次数上限.
- `NG-english-support`: 不实现英语变式.
- `NG-large-skill-shift`: 不接受重大难度/技能变化.
- `NG-failed-history`: 不永久保存全部失败历史.
- `NG-hidden-reasoning`: 不保存或公开隐藏推理.
- `NG-post-submit-validation`: 不增加入库后再验证.
- `NG-partial-submit-ui`: 不增加部分提交按钮.
- `NG-new-policy-framework`: 不增加假想 policy/registry/CAS 数学引擎.
