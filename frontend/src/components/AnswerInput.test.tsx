import { describe, it, expect } from "vitest";
import { parseOptions, extractOptionKey } from "./AnswerInput";

describe("parseOptions", () => {
  it("returns lettered choices when present", () => {
    const text = "1. — Could I use your bike?\n— Yes, you ________. But you ________ keep it clean.\nA. could; can\nB. can; must\nC. must; can\nD. could; must";
    const result = parseOptions(text);
    expect(result).toHaveLength(4);
    expect(result[0]).toMatch(/^A/);
    expect(result[1]).toMatch(/^B/);
    expect(result[2]).toMatch(/^C/);
    expect(result[3]).toMatch(/^D/);
  });

  it("ignores numeric question stems when lettered choices exist", () => {
    const text = "1. What is 2+2?\n2. What is 3+3?\nA. 4\nB. 5\nC. 6\nD. 7";
    const result = parseOptions(text);
    expect(result).toHaveLength(4);
    expect(result.every((r) => /^[A-Z]/.test(r))).toBe(true);
  });

  it("returns numeric choices when no lettered choices exist", () => {
    const text = "Choose the correct answer:\n1. First option\n2. Second option\n3. Third option";
    const result = parseOptions(text);
    expect(result).toHaveLength(3);
    expect(result[0]).toMatch(/^1/);
  });

  it("returns empty array when no options are found", () => {
    const text = "This is just plain text\nwith no option markers\nat all.";
    const result = parseOptions(text);
    expect(result).toEqual([]);
  });

  it("handles single choice with just A and B", () => {
    const text = "True or false?\nA. True\nB. False";
    const result = parseOptions(text);
    expect(result).toHaveLength(2);
  });

  it("handles lettered options with parenthesis format", () => {
    const text = "Pick one:\nA) Alpha\nB) Beta\nC) Gamma";
    const result = parseOptions(text);
    expect(result).toHaveLength(3);
  });

  it("handles lettered options with colon format", () => {
    const text = "Pick one:\nA: Alpha\nB: Beta";
    const result = parseOptions(text);
    expect(result).toHaveLength(2);
  });
});

describe("extractOptionKey", () => {
  it("extracts letter key from lettered option", () => {
    expect(extractOptionKey("A. could; can")).toBe("A");
  });

  it("extracts numeric key from numeric option", () => {
    expect(extractOptionKey("1. First option")).toBe("1");
  });

  it("returns trimmed option when no marker is found", () => {
    expect(extractOptionKey("no marker")).toBe("no marker");
  });
});