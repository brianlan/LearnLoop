export const PROBLEM_TYPE_OPTIONS = [
  { value: "single-choice", label: "Single Choice" },
  { value: "multi-choice", label: "Multi Choice" },
  { value: "fill-in-the-blank", label: "Fill in the Blank" },
  { value: "short-answer", label: "Short Answer" },
];

export const PROBLEM_TYPE_FILTER_OPTIONS = [
  { value: "", label: "All Types" },
  ...PROBLEM_TYPE_OPTIONS,
];
