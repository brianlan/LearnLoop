# 变式入库实施方案草稿

状态：产品行为与核心设计已在需求讨论中确认，包含 Q1–Q4 及入库后 variation 不可变的确认；尚未实施功能代码。

依据：最新用户确认优先，其次是需求文档与本实施方案。难度 category、helper 验答及入库后编辑行为按本文记录的已确认决定执行。v2 作为参考，不整体沿用。

## 1. 已确定的产品规则

- 三种模式在创建 batch 前选择，batch 内不可修改；省略模式沿用 Original。
- 每题由用户校对原题、提供答案后点击 Generate。点击动作即确认本次原题，不额外增加确认步骤。
- 生成与验证在后台持续运行，用户可以处理其他题。
- 答案是否一致交给已有 helper VLM 判断，支持不同表达形式、多问、多空；不使用现有字符串归一化作为验证硬门槛。
- 独立验证者先盲解原题与候选，不接收预期答案、生成者推理或另一验证者报告。
- 评价直接使用合并后的 category。category 的差异参与判断，解释文字的差异不参与。
- 候选题型必须与确认原题一致。不一致则 FAIL，展示原题型、候选题型及依据，不自动修改成一致，也不自动重新生成。
- 只有当前版本的 PASS 候选可以进入正常终审和提交；失败无强行接受或原题回退路径。
- 入库前修改语义字段使 PASS 失效；修改标签不需要重新验证。
- 入库后，变式和普通题使用相同字段、相同编辑流程，不再验证。
- 原题、审计图、成功验证报告、模型来源和生成次数永久保留；它们记录入库时的事实。
- 首版数学规则，不增加额外的自动语言或学科识别步骤。

沿用现有体验：整批审核后提交，不新增“提交已通过子集”按钮；沿用现有 batch 有效期。

## 2. 从当前代码得出的约束

| 当前 Implementation | 必须采取的措施 |
|---|---|
| `infrastructure/ingestion/repository.py` 多条路径读完整 batch，再覆盖整个 `items` | 将会并发经过的 item 修改改成定点更新，覆盖提取完成、编辑、删除/撤销、提交和创建 items 等写入 |
| `domain/normalization.py` 会删除 `%`、`^`、逗号等数学符号 | 不将其作为变式验答；永久 Problem 的答案存储仍沿用现有格式 |
| `presentation/problems.py:UpdateProblemRequest` 编辑 text、problemType、graphDsl、correctAnswer、tags | 变式入库后复用同一编辑路径；不让普通编辑请求写入历史原题或验证报告 |
| `VLMClient` 的请求基类强制需要图片 | 新的文本请求不继承这个图片不变量；简答判分改为支持可选图片和 GraphDSL |
| practice/exam 简答判分目前没有传 GraphDSL | 两条调用路径同时补齐，防止含图变式缺少解题上下文 |
| GraphDSL sanitizer 检查安全语法，不证明数学正确或元素可运行 | 保留安全检查，独立验证图文关系；新 prompt 使用其实际支持的语法 |
| Mongo 已有 replica set、`MongoClientAdapter.start_session()` 和考试提交事务 | 用现成短事务保证变式创建与 item 提交状态一致，无需另建事务框架 |

相关代码：

- [入库 repository](../backend/app/infrastructure/ingestion/repository.py)
- [已有丢更新复现场景](../backend/tests/integration/test_ingestion_atomicity.py)
- [Problem 创建](../backend/app/presentation/problem_creation.py)
- [考试提交事务](../backend/app/presentation/exams.py)
- [VLM 请求与判分](../backend/app/infrastructure/vlm/client.py)
- [前端自动保存](../frontend/src/components/BulkReviewStep.tsx)

## 3. Module 与 Seam

新增一个变式 workflow Module，建议主文件为 `app/problem_variation.py`。
它的 Interface 向调用方表达四个操作：

1. 从本次确认的原题请求生成。
2. 修改候选，返回当前有效性。
3. 请求重新验证已修改的候选。
4. 提交当前通过验证的候选。

状态转换、版本检查、答案比较结果聚合、失败证据及提交准入集中在这个 Module。
HTTP handler、worker 和前端不分别重新定义 PASS 条件。

持久化 Seam 留在 ingestion repository。模型 Adapter 使用现有 `BaseVLMClient` 传输；
生成、独立验证、答案等价判断分别使用自己的文本请求与 prompt。
依赖由调用方传入，测试可以传入假模型 Adapter。

纯粹的报告聚合与状态判断可以留在 `domain/ingestion/variation.py`。
这里是 workflow 的 internal seam，不引入通用 policy、repository protocol 或模型 registry。

## 4. 数据与版本

保持现有 batch/items 存储结构。新增 batch.ingestionMode，以及 item 的变式状态。

```text
item.draft                       可编辑原题；标签仍只保留这一份
item.contentRevision             原题/候选语义变化或新生成时递增
item.variation:
  status
  generationCount                仅 Generate / Generate Again 增加
  original                       本次确认的原题快照
  candidate                      text / problemType / graphDsl / correctAnswer
  validation                     报告、helper 比较、最终结论、失败依据
  validatedRevision              PASS 对应的 contentRevision
  claimToken / leaseUntil        worker ownership
```

不保存永久失败尝试历史，不计算内容 fingerprint。
当前失败候选与证据可以留在 batch 中，直到用户再次生成或 batch 清理。

所有操作遵守：

- 编辑请求附带预期 contentRevision，避免旧标签页的语义内容覆盖新版本。
- 只比较实际字段值是否变化；不能因为 PATCH 出现了 text 等字段就失效。
- 标签编辑不递增 contentRevision，也不影响在途验证。
- 新生成使旧候选身份失效；旧候选保存请求不得写入新候选。
- 验证记录只有在 validatedRevision 等于当前 contentRevision 时有效。
- token 防止被替代的 worker 写回；版本防止用户确认或编辑已经过时的内容。
- worker 状态写入不递增内容版本；使用当前写入时间记录 updatedAt。

## 5. 工作流与状态

```text
not-requested → queued → generating → validating → ready | failed
ready → needs-validation
needs-validation → queued → validating → ready | failed
failed → queued                    用户点击 Generate Again
```

queued 进入 worker 后，有 candidate 则验证，无 candidate 则生成。
Generate Again 必须先清除旧 candidate；重新验证保留当前 candidate 与确认原题。
重新验证不增加 generationCount。

修改原题语义字段：原子递增版本、清除旧候选与有效性、取消在途 ownership，回到 not-requested。
修改候选语义字段：递增版本并进入 needs-validation。
删除取消 ownership；撤销删除后只恢复仍与当前版本匹配的结果，未完成工作重新排队。

已失败候选只作为证据展示，不进入正常终审；重新生成由用户触发。

worker 只 claim 未过期的 active batch 和未删除、未提交的 item。
写回同样检查 batch 有效期、item 状态、当前版本与 token，不能只检查 batch.status。
借鉴考试判分 worker 的租约/token；不照搬 extraction 的整数组结果写回。
生成结果先持久化，再调用验证者；进程重启后可从候选继续验证。
租约覆盖实际调用预算，长调用续租；模型调用不放进数据库事务。

## 6. 模型契约与 category

生成者返回完整候选：text、problemType、graphDsl、correctAnswer。
subject 继承原题，tags 由 item.draft.tags 提供，不交给生成者改写。
保留模型实际返回的题型，以便检测和展示不一致，不静默强制改成原题型。

独立验证者接收 mode、原题和候选的 text/problemType/graphDsl；不接收答案与图片。
结构化报告包含两题独立答案，以及以下评价：

| 评价 | category | 通过值 |
|---|---|---|
| 原题、候选是否可解且条件充分 | yes / no | yes |
| 核心知识是否保持 | preserved / changed | preserved |
| 解法结构与已知/未知量角色是否保持 | preserved / changed | preserved |
| 难度变化 | comparable / materially-easier / materially-harder | comparable |
| 数值计算复杂度变化 | comparable / materially-easier / materially-harder | comparable |
| 表示方式或所需技能变化 | none-or-nonmaterial / material | none-or-nonmaterial |
| 模式遵循 | compliant / noncompliant | compliant |
| 图文一致性 | consistent / inconsistent / not-applicable | consistent；无图时允许 not-applicable |
| 是否实际改变数学数据 | changed / unchanged | changed |

comparable 明确定义为原五档中的“略容易、基本相同、略难”。
明显变容易和明显变难仍分别记录，以便解释失败。
必要的表示、符号、精确整除、单位换算等技能变化不能用“总体 comparable”掩盖。
图存在时不能报告 not-applicable。

报告还返回简短的判定依据与可读解答摘要，不请求、暴露或保存隐藏推理。
无法求解时允许返回空答案，并明确原因，随后 FAIL。

只有一个或两个独立验证者，不实现任意模型投票或加权评分。
两者的关键 category 冲突时展示冲突字段；自由解释文本不作相等比较。
所有必要 category 均为通过值时，关键判断自然一致，无需额外“轻微档位兼容”逻辑。

## 7. helper VLM 验答

复用现有 helper_vlm 配置；新增文本答案等价操作，不借用已有的学生答题评分 prompt。

每个验证者完成独立报告后，helper 比较两对答案：

1. 该验证者的原题答案 vs 用户确认原题答案。
2. 该验证者的候选答案 vs 生成/编辑后的候选答案。

一个 helper 请求可比较同一验证者的两对答案。
请求包含各自题干、题型、GraphDSL 和答案，保证多问、多空的对应关系清楚。
不提供其他验证者结果，不能通过“多数一致”覆盖某个错误答案。

每对返回 equivalent / different / uncertain，及简短依据。
所有要求的答案对均 equivalent 才可能 PASS；different 或 uncertain 都阻止通过。
helper 不补答、不修正任何一边，不把部分正确判为完整等价。
应检查题目要求的答案形式、单位、每个子问和空的对应关系，而非只比较数值。
超时/无效响应是验证执行失败，不能伪装成答案不一致。

生成者、验证者、helper 的 provider 错误沿用已有错误分类。
可重试传输错误做有限自动重试；最终失败显示清楚，用户可以 Generate Again。
不存在 helper 不可用时退回旧 normalization 的路径。

## 8. 服务器 PASS 与失败证据

服务器根据结构化事实聚合，不直接接受模型自报 verdict。

PASS 必须同时满足：

- 候选字段完整，GraphDSL 通过安全语法检查。
- 候选题型与确认原题完全相同。
- 每个验证者均独立完成两题求解，所有必要 category 均通过。
- 每个验证者的两对答案均经 helper 判定 equivalent。
- 没有关键 category 冲突，且报告仍对应当前内容版本。

失败信息应包括实际依据：原题型/候选题型；预期答案/独立答案/helper 判断；
失败 category、验证者名称与简短说明；模型调用错误的执行阶段。
不做全文解释字符串比较，不自动重新生成，不提供接受失败候选的入口。

## 9. 提交、图片与永久记录

变式提交使用现成 Mongo 短事务，保护读取 PASS 到保存 Problem 的整个数据库操作。

1. 读取通过版本，复制原 crop 至稳定的永久审计目标键；目标与 batch/item 对应，重试不创建新副本。
2. 事务内重新读取 item，确认 active、未过期、未删除、当前版本仍通过。
3. 创建变式 Problem，并原子写入 item.submitted 与 Problem ID。
4. 同一事务安排 solution task；标签注册沿用已有能力，可在提交后幂等补齐。

两个并发提交会在同一 batch/item 写入处发生事务冲突；重试重新读取 submitted 状态后返回已创建的 Problem。
不能只依赖现有 origin 的 find_one → insert 来保证并发幂等。
必要时从 create_problem_from_draft 提取普通与变式两条真实路径共用的文档构造逻辑，不复制字段组装。
不新增通用事务抽象，所有事务内数据库操作必须使用同一个 session。

审计复制在事务外完成，不能放入可自动重试的事务回调。
事务失败不会创建可见 Problem；提交重试保留 candidate/PASS，不能重提取原题。
过期 batch 的未提交审计副本可清理，清理前确认没有永久 Problem 引用；已提交副本永久保留。

永久 Problem 使用正常题目字段保存候选，sourceImage=null。另加 variation：

- 确认原题及永久 auditImage。
- 入库时通过的候选快照。
- mode、生成模型、独立验证模型、helper 模型。
- 成功结构化报告、答案比较报告、PASS、生成次数。

成功快照用于解释入库时的验证记录；入库后正常编辑不改写它。
历史原题和审计图通过 Problem 页折叠区域查看，不进入练习、考试、Solution 或 Coaching 上下文。

### 9.1 永久 Problem.variation 的具体 sub schema

以下字段名为本方案建议，尚未写入生产模型。普通题的 variation 缺省或 null。
用 TypeScript 记法表达存储结构，CorrectAnswer、SourceImage、ProblemType、ProblemSubject 复用现有定义。

```typescript
type ModelIdentity = { provider: string; model: string };

type ProblemContentSnapshot = {
  text: string;
  problemType: ProblemType;
  subject: ProblemSubject;
  graphDsl: string | null;
  correctAnswer: CorrectAnswer;
};

type ProblemVariation = {
  mode: "data-only" | "data-and-wording";
  original: ProblemContentSnapshot & { auditImage: SourceImage };
  acceptedVariant: ProblemContentSnapshot;
  generator: ModelIdentity;
  generationCount: number;
  validation: {
    verdict: "pass";
    helperModel: ModelIdentity;
    reports: ValidatorReport[]; // 长度为 1 或 2
  };
};

type Check<T extends string> = { category: T; evidence: string };
type Preservation = "preserved" | "changed";
type ComplexityShift = "comparable" | "materially-easier" | "materially-harder";

type AnswerComparison = {
  result: "equivalent" | "different" | "uncertain";
  evidence: string;
};

type ValidatorReport = {
  validatorModel: ModelIdentity;
  originalSolvedAnswer: string;
  variantSolvedAnswer: string;
  originalSolutionSummary: string;
  variantSolutionSummary: string;
  checks: {
    originalWellPosed: Check<"yes" | "no">;
    variantWellPosed: Check<"yes" | "no">;
    coreKnowledge: Check<Preservation>;
    solutionStructure: Check<Preservation>;
    quantityRoles: Check<Preservation>;
    difficultyShift: Check<ComplexityShift>;
    numericComplexityShift: Check<ComplexityShift>;
    representationShift: Check<"none-or-nonmaterial" | "material">;
    modeCompliance: Check<"compliant" | "noncompliant">;
    graphConsistency: Check<"consistent" | "inconsistent" | "not-applicable">;
    dataChange: Check<"changed" | "unchanged">;
  };
  answerComparison: {
    original: AnswerComparison;
    variant: AnswerComparison;
  };
};
```

CorrectAnswer 包含 display、normalizedText、normalizedSet、format；
SourceImage 包含 bucket、objectKey，以及 contentType、sizeBytes、sha256、uploadedAt 等已有可选字段。
快照不重复 tags、tracking、origin 或可编辑状态。

永久 validation 只保存成功记录，因此 answerComparison 的实际值必须是 equivalent，
各 checks 必须是第 6 节规定的通过值。完整 category 枚举用于与入库会话报告共用表达。
evidence 与 solutionSummary 为简短可读依据，不保存模型隐藏推理或完整 provider 响应。

acceptedVariant 入库时与 Problem 的主内容相同。之后修改题目只更新主字段，
acceptedVariant、original 和 validation 均保持原值。
这是“当时通过什么内容”的记录，不是供练习或编辑读取的第二份当前题目。

worker 的 status、claimToken、leaseUntil、contentRevision、validatedRevision 留在 ingestion item，
不进入永久 Problem.variation。

## 10. 前端与下游接入

- BulkUploadStep 创建前选择模式；恢复 batch 显示锁定模式。
- 原题校对与候选终审明确区分编辑目标，草稿身份包含 item、目标和候选版本。
- Generate 原子提交当前校对内容和预期版本，再请求生成，避免依赖 debounce 的完成顺序。
- 终审内容修改先保存成功，才允许重新验证或提交；保存失败保留本地输入与局部错误。
- 轮询覆盖 queued/generating/validating，不覆盖未保存输入；较早请求的迟到响应不回退新显示。
- 标签仍是同一份可编辑元数据，不因阶段切换而丢失。
- 修改原题后明确提示需要重新生成；失败页面展示证据与 Generate Again。
- 前端 Continue 要求全部未删除/未提交题通过当前版本验证；服务器独立执行相同准入规则。
- ProblemDetailPage 继续通过现有 PATCH 编辑 text/problemType/graphDsl/correctAnswer/tags。
- 原题、审计图与成功记录不加入该编辑表单，也不新增入库后再验证操作。
- 练习/考试的简答判分补充可选图片与 GraphDSL；图片提取和检测仍要求图片。

## 11. 实施顺序与验收

1. **持久化与版本一致性**：定点修改、内容版本、状态转换和短事务提交。
   验收：真实 Mongo 证明编辑 B 不覆盖 A，过期 worker 无法写回，编辑与提交并发不会保存失效候选，并发提交只创建一个 Problem。
2. **模型与确定性聚合**：生成、盲解、helper 比较、category 和失败证据。
   验收：假模型覆盖全部硬门槛；1/2 与 0.5 可等价，50% 与 50 不等价，多问缺答失败，题型不一致失败，category 冲突失败。
3. **前端、审计与实际使用链路**：模式、双编辑目标、后台状态、永久副本和文本/GraphDSL 判分。
   验收：未保存答案点击 Generate 使用本次输入；切换题目/刷新后任务继续；旧保存不污染新候选；语义编辑失效而标签不失效；入库后可按普通题编辑，且不重新验证。
4. **端到端与质量样本**：一/两个验证者、生成失败、重新生成、修改后再验证、提交重试及批次清理。
   验收：原模式行为保持；原图永不进入变式模型上下文；审计图经清理后仍可查看；练习与考试均可处理纯文本和 GraphDSL 简答题。

实现测试优先使用 scripts/agent-env.sh。并发/事务检查进入已有 backend-real 测试入口；
前端使用现有 Vitest/Playwright，不另建测试框架。
另外选取需求中的整除、分数、单位、计算复杂度和图文变更案例做真实模型质量核验；
假模型测试证明程序 gate 正确，不能证明模型的教学判断可靠。

## 12. 明确不新增的机制

不新增独立 attempt 历史集合、双内容 fingerprint、任意模型投票、学科策略框架、
数学符号计算引擎、批量生成、部分提交交互、失败人工 override 或入库后验证。
保留成功证据，不永久保存全部失败候选或模型隐藏推理。
