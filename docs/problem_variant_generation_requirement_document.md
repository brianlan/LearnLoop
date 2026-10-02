# LearnLoop Variant Ingestion — Product Requirements

## 1. Overview

LearnLoop should support a new ingestion workflow that can transform an ingested problem into a slightly modified variant before the problem is permanently added to the problem library.

The purpose of this feature is not to generate arbitrary similar problems. It is to create a new problem that remains pedagogically very close to the original problem:

* it should test essentially the same knowledge,
* use essentially the same reasoning structure,
* remain at approximately the same difficulty,
* and differ primarily in the concrete data and, optionally, the wording or story context.

The original uploaded problem acts as the source material. In a variant-ingestion workflow, the final problem saved into LearnLoop is the generated variant rather than the original problem.

The initial version of this feature targets mathematics problems only.

---

## 2. Goals

The feature should make it easy to turn an existing problem into a fresh but strongly equivalent variant suitable for future practice.

The generated variant should preserve the educational intent of the source problem while reducing the chance that a student can answer purely from memory of the original wording or numbers.

The system should also provide a strong automated verification process before a generated variant is accepted for normal review.

The final decision should remain transparent to the user: failed variants should not silently fall back to the original problem.

---

## 3. Ingestion Modes

At the beginning of a new ingestion session, before the batch is created and before images are uploaded, the user should choose one of three ingestion modes:

```text
Ingest mode

○ Original
○ Change data only
○ Transfer Variant
```

> Naming note (#656): the third mode's canonical name is **`transfer-variant`**
> (UI: **Transfer Variant**). It generates a genuinely new problem from the
> source's abstract mathematical blueprint rather than merely changing data
> and wording. The historical value `data-and-wording` survives only as a
> legacy read-only provenance value; it is not offered for new batches.

A single ingestion session uses one mode consistently.

### 3.1 Original

The existing ingestion behavior remains unchanged.

The original problem is extracted, reviewed by the user, and saved without automatic transformation.

### 3.2 Change Data Only

The system creates a variant in which the problem structure and wording remain as close as possible to the original, while the concrete mathematical data changes.

For example:

```text
Original:
小明有12个苹果，送给小红5个，还剩几个？

Variant:
小明有17个苹果，送给小红8个，还剩几个？
```

In this mode, changes such as the following are allowed:

* numerical values,
* percentages,
* ratios,
* lengths,
* angles,
* coordinates,
* numeric values in answer choices,
* numeric labels or values represented in GraphDSL.

Changes such as the following should not occur:

* 小明 → 小红
* 苹果 → 铅笔
* “还剩几个” → “现在还有多少”
* changing the story context,
* changing the semantic role of a quantity,
* changing what the problem asks the student to find.

The goal is to preserve the lexical and semantic template of the original problem as strictly as practical while changing its data.

### 3.3 Change Data + Wording

The system may change both the mathematical data and the wording or surface context, while keeping the mathematical structure essentially unchanged.

For example:

```text
Original:
小明有12个苹果，送给小红5个，还剩几个？

Variant:
文文买了18支铅笔，送给同学7支，现在还有多少支？
```

Allowed changes may include:

* numbers and other mathematical data,
* names,
* objects,
* light story-context changes,
* paraphrasing,
* reordering or rephrasing natural-language expressions.

However, the following must remain effectively unchanged:

* the core mathematical relationship,
* the knowledge being tested,
* the role of known and unknown quantities,
* the reasoning direction,
* the required solution strategy,
* the approximate level of difficulty.

This mode is intended to produce a different-looking problem that is still, pedagogically, almost the same problem.

---

## 4. Variant Ingestion Workflow

A variant ingestion workflow should conceptually follow this sequence:

```text
Choose ingestion mode
        ↓
Upload
        ↓
Problem detection / cropping
        ↓
Extraction
        ↓
User prepares and confirms the original problem
        ↓
Generate variant
        ↓
Independent validation
        ↓
PASS
        ↓
Normal final review
        ↓
Submit
```

A failed variant does not proceed to normal final review.

---

## 5. Original Problem Preparation

Variant generation must not begin immediately after extraction.

The user must first review the extracted source problem and correct any extraction errors.

The user should be able to inspect and edit at least:

* problem text,
* problem type,
* GraphDSL,
* correct answer.

The original crop remains visible to help the user compare the structured extraction with the uploaded problem.

The correct answer is mandatory.

A variant cannot be generated until the user has supplied or confirmed the correct answer of the original problem.

This requirement is important because the system should generate variants from a user-confirmed interpretation of the source problem rather than from potentially incorrect extraction output.

When the user starts variant generation, the current reviewed version of the original problem becomes the authoritative source version for that generation attempt.

If the user later changes information that affects the meaning or solution of the original problem, the previously generated variant and its validation result should no longer be considered valid, and a new generation attempt is required.

---

## 6. Per-Problem Generation

Variant generation should be initiated individually for each problem.

The user should be able to:

1. review the extraction of one problem,
2. fill or correct its answer and other fields,
3. click **Generate**,
4. move immediately to the next problem while generation and validation continue in the background.

The user should not have to wait for one problem to finish before preparing the next problem.

The first version does not need a “Generate all” action for the entire batch.

---

## 7. Variant Generation Requirements

The variant generator should produce a complete, internally consistent candidate problem.

The candidate must include all information affected by the transformation, including:

* problem text,
* correct answer,
* GraphDSL when applicable.

Fields that define the identity or educational category of the source problem should not be casually changed.

In particular, a generated variant should preserve the original:

* subject,
* problem type,
* intended educational domain.

### 7.1 Core Pedagogical Equivalence

A valid variant should satisfy all of the following:

* the same or nearly the same core knowledge is required,
* the same or nearly the same reasoning pattern is required,
* the student can solve both problems using essentially the same solution template,
* the overall difficulty is approximately the same,
* the computational burden does not change substantially,
* the problem remains well posed and unambiguous.

A slight difficulty or numerical-complexity shift is acceptable.

A qualitative change in required skill is not acceptable.

For example:

```text
48 ÷ 6
```

should not become:

```text
47 ÷ 6
```

if the original educational intent is exact integer division, because the latter may introduce remainder or fractional reasoning.

Likewise:

```text
24 × 3
```

should not become:

```text
327 × 48
```

if the increased arithmetic complexity materially changes the difficulty of the exercise.

---

## 8. Representation and Skill Preservation

A variant should not introduce a materially different mathematical representation or skill merely as a side effect of changing values.

Examples of potentially unacceptable shifts include:

* integer → fraction,
* integer → decimal,
* positive number → negative number,
* exact division → remainder,
* exact division → fractional result,
* proper fraction → improper fraction,
* simple fraction → fraction requiring substantial simplification,
* acute-angle reasoning → obtuse-angle reasoning when that distinction matters,
* no unit conversion → required unit conversion,
* no carrying/borrowing → substantially more complex arithmetic when that changes the intended difficulty.

A small increase or decrease in arithmetic effort is acceptable as long as the problem remains recognizably at the same level and does not require a new skill.

---

## 9. GraphDSL Consistency

If the original problem contains GraphDSL and the transformed data affects the graph or diagram, the GraphDSL must be updated consistently.

For example, if a problem changes:

```text
AB = 5 cm
BC = 8 cm
```

to:

```text
AB = 7 cm
BC = 11 cm
```

the text, answer, and diagram representation must agree with each other.

A variant must not pass validation if its GraphDSL contradicts the problem statement or preserves stale values from the original problem.

---

## 10. Independent Validation

Every generated variant must be validated before it can enter the normal final-review stage.

Validation is a hard gate.

The system may be configured with:

* one validator model, or
* two independent validator models.

When two validators are configured, both must independently evaluate the same original/variant pair.

The validators should not rely on the generator’s internal reasoning.

They should independently solve and assess both problems.

The validator should receive the structured original and variant problems, including textual problem information and GraphDSL when available.

The validation process should not depend on the original uploaded image.

---

## 11. Independent Solving

Each validator must independently solve:

1. the confirmed original problem,
2. the generated variant.

The validator must return an answer for both.

The system must compare:

```text
Validator's original answer
vs.
User-confirmed original answer
```

and:

```text
Validator's variant answer
vs.
Generated variant answer
```

An answer inconsistency is a hard validation failure.

If two validator models are configured, both validators must successfully solve both problems consistently.

---

## 12. Similarity and Equivalence Assessment

In addition to solving the problems, each validator should assess whether the original and variant remain pedagogically equivalent.

The assessment should cover at least the following areas.

### 12.1 Core Knowledge

Does the variant require essentially the same mathematical concepts and knowledge as the original?

A meaningful knowledge-point shift causes validation failure.

### 12.2 Solution Structure

Can both problems be solved using essentially the same reasoning structure and solution method?

Problems that belong to the same broad topic but reverse the reasoning direction should not automatically be considered equivalent.

For example:

```text
Given original price and discount, find sale price
```

is not necessarily equivalent to:

```text
Given sale price and discount, recover original price
```

even though both involve percentages.

### 12.3 Difficulty Shift

The validator should assess whether the variant is:

```text
much easier
slightly easier
approximately the same
slightly harder
much harder
```

Small shifts are acceptable.

Large shifts cause validation failure.

### 12.4 Numerical Complexity Shift

The validator should separately assess the computational complexity of the concrete values.

It should consider factors such as:

* size and digit count of numbers,
* decimal precision,
* fraction complexity,
* carrying or borrowing,
* exact versus non-exact division,
* fraction simplification,
* number of arithmetic steps,
* unit conversion,
* sign changes.

Small changes are acceptable.

Material increases or decreases cause validation failure.

### 12.5 Representation Shift

The validator should identify whether the changed data introduces a different mathematical representation or a new skill requirement.

A material representation shift causes validation failure even if the overall topic appears similar.

### 12.6 Mode Compliance

For **Change Data Only**, the validator should verify that the wording and semantic template were preserved except for the necessary changed data.

For **Change Data + Wording**, wording and context changes are allowed, but the underlying mathematical roles and reasoning structure must remain substantially unchanged.

### 12.7 Graph Consistency

The validator should check whether GraphDSL, when present, remains consistent with the problem text.

### 12.8 Ambiguity and Well-Posedness

The variant must remain solvable, sufficiently specified, and unambiguous.

---

## 13. Validation Outcome

Validation should produce a simple final outcome:

```text
PASS
FAIL
```

Only `PASS` may proceed to normal final review.

There is no “accept anyway” path for a failed candidate.

### 13.1 Single Validator

A candidate passes only if:

* the validator passes all required checks,
* the validator solves the original consistently with the confirmed original answer,
* the validator solves the variant consistently with the generated answer.

### 13.2 Two Validators

A candidate passes only if:

* Validator A passes,
* Validator B passes,
* both validators independently solve both problems correctly,
* their important structured judgments are consistent.

If the validators disagree on a material validation judgment, the candidate fails.

Free-form explanatory wording does not need to be identical between validators.

For example:

```text
Validator A:
sameCoreKnowledge = true

Validator B:
sameCoreKnowledge = false
```

must produce `FAIL`.

However:

```text
Validator A:
"Both require rate × time."

Validator B:
"The same multiplication relationship is used."
```

does not constitute disagreement merely because the explanations use different words.

---

## 14. Failed Generation or Validation

The system must never silently replace a failed variant with the original problem.

If generation or validation fails, the problem should clearly show a failed state.

The user should be able to see useful failure information, for example:

```text
Variant validation failed

✗ Variant answer mismatch
  Generated answer: 42
  Validator A: 46

✗ Numeric complexity increased substantially

✓ Core knowledge preserved
✓ Solution structure preserved
```

The user should then be able to choose:

```text
Generate Again
```

A new generation attempt should create a new candidate and run the complete validation process again.

There is no limit on the number of Generate Again attempts in the initial version.

---

## 15. Background Processing

After the user clicks **Generate**, generation and validation should continue independently of the user's current position in the ingestion workflow.

The user should be able to move to another extracted problem and continue preparing it while the previous problem is being processed.

Returning to the earlier problem should show its current state, such as:

```text
Generating
Validating
Failed
Ready
```

The user should not need to keep the problem open for generation or validation to finish.

---

## 16. Final Variant Review

Only a variant that has successfully passed validation may enter the normal final review stage.

The initial version does not require a side-by-side original-versus-variant comparison.

The normal review view should primarily display the variant.

The original crop should still be visible, allowing the user to visually refer back to the source problem.

The user should be able to inspect the final variant fields before submission.

If the user manually modifies a field that affects the meaning or solution of an already validated variant, the previous validation result is no longer valid and the problem must be validated again before it can be submitted.

This applies to fields such as:

* problem text,
* problem type,
* GraphDSL,
* correct answer.

Changes to non-semantic metadata such as tags do not require revalidation.

---

## 17. Final Saved Problem

For variant ingestion, the generated and validated variant is the problem that is permanently saved into LearnLoop.

The original extracted problem is not also saved as a separate practice problem.

This is a **1 → 1 replacement** workflow:

```text
source problem
    ↓
generated variant
    ↓
saved Problem
```

---

## 18. Original Source Retention and Auditability

Although the original problem is not saved as a normal practice problem, LearnLoop should permanently retain enough information to trace the variant back to its source.

This provenance should include the user-confirmed structured original problem, including:

* original text,
* original problem type,
* original GraphDSL,
* original correct answer.

The original crop image should also be retained permanently for audit and later inspection from the Problem page.

This audit image must be conceptually separate from the image used as active solving context.

For a variant problem:

* the original crop is historical/audit evidence,
* it must not be shown to the Solution model as part of the new variant,
* it must not be shown to the Coaching model as if it described the new variant.

This distinction is important because the text and numerical values of the saved variant may intentionally differ from the original image.

---

## 19. Validation Provenance

The final saved variant should retain sufficient validation provenance to understand how it was approved.

This should include, at a product level:

* which generation system produced the accepted variant,
* which validator model or models validated it,
* the successful structured validation reports,
* the final PASS result,
* the number of generation attempts required before success.

Detailed failed candidates do not need to be permanently attached to the final Problem.

They may be retained during the ingestion session for operational visibility.

The system does not need to preserve or expose hidden chain-of-thought reasoning from the generator or validators.

---

## 20. Mathematics-Only Initial Scope

The first release of variant ingestion is intended for mathematics problems.

The product architecture should not conceptually assume that variant generation can only ever support mathematics.

Future subject-specific behavior may differ significantly.

For example, a future English variant workflow may require different rules for:

* grammar exercises,
* vocabulary,
* reading-comprehension passages,
* answer choices,
* sentence transformations.

The shared workflow should therefore conceptually support subject-specific variant policies in the future, while only mathematics behavior needs to be implemented in the first version.

The first version does not need additional automatic language/subject policing beyond the normal ingestion process. The user remains responsible for using the variant workflow appropriately.

---

## 21. Quality Principles

A successful variant is not merely a problem with similar wording.

The desired outcome is a **pedagogically equivalent variant**.

The quality bar is therefore:

```text
Same core knowledge
+
Same or nearly same solution structure
+
Approximately same difficulty
+
Approximately same numerical complexity
+
No new required skill
+
Internally consistent text / answer / graph
+
Compliant with the selected variation mode
```

A slight quantitative difference in difficulty or arithmetic workload is acceptable.

A qualitative change in the skill being exercised is not.

---

## 22. Key Product Invariants

The following rules should hold throughout the feature:

1. A user must confirm the original problem and provide its correct answer before variant generation.

2. Variant generation is initiated per problem.

3. Generation and validation may continue in the background while the user works on other problems.

4. The generator produces a new variant rather than modifying the permanent original Problem.

5. Validation independently solves both the original and the variant.

6. Any answer mismatch is a hard failure.

7. A meaningful shift in knowledge, reasoning structure, representation, or difficulty is a failure.

8. With two validators, material disagreement between validators is a failure.

9. Only a validated `PASS` candidate may enter normal final review.

10. A failed candidate can only be replaced by generating and validating a new candidate.

11. The system must never silently fall back to the original problem.

12. Semantic edits made after validation invalidate the previous validation result.

13. The final Problem is the variant only.

14. The original structured problem and crop remain permanently traceable for audit purposes.

15. The original crop must never be treated by Solution or Coaching as visual context for the transformed variant.

16. The initial release supports mathematics while leaving room for future subject-specific variation rules.

---

## 23. Out of Scope for the Initial Version

The following are not required for the first release:

* automatic generation of multiple variants from one source problem,
* saving both the original and variant as separate practice problems,
* batch-wide “Generate All” behavior,
* automatic fallback to the original when variant generation fails,
* manual override of failed validation,
* side-by-side original/variant comparison UI,
* a fixed retry limit for Generate Again,
* automatic support for English variant generation,
* automatic acceptance of large difficulty shifts,
* permanent storage of every failed generation attempt,
* exposure or storage of model chain-of-thought reasoning.

---

## 24. Example End-to-End Scenario

The user uploads a worksheet containing:

```text
小明有12个苹果，送给小红5个，还剩几个？
```

The extraction system produces:

```text
text:
小明有12个苹果，送给小红5个，还剩几个？

correctAnswer:
[empty]
```

The user checks the crop, confirms the extracted text, and fills:

```text
correctAnswer:
7
```

The user chose:

```text
Change data + wording
```

and clicks:

```text
Generate
```

The user then moves to the next problem while processing continues.

The generator produces:

```text
文文买了18支铅笔，送给同学7支，现在还有多少支？
```

with:

```text
correctAnswer:
11
```

Validator A independently solves:

```text
Original answer: 7
Variant answer: 11
```

and reports:

```text
sameCoreKnowledge: true
sameSolutionStructure: true
difficultyShift: approximately the same
numericComplexityShift: approximately the same
representationShift: none
modeCompliance: true
ambiguityIntroduced: false
verdict: PASS
```

If a second validator is configured, it independently performs the same task and reaches a compatible PASS result.

The variant then becomes available for normal final review.

After submission, LearnLoop permanently saves the variant as the practice problem while retaining the original structured problem and crop as audit provenance.

The saved variant is solved and coached using the variant text, answer, and GraphDSL only. The original crop is never treated as solving context.
