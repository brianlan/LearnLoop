import { describe, it, expect } from "vitest";

import type { FolderNode } from "@/types/folder";
import {
  buildAncestorMap,
  findFolder,
  flattenFolders,
  getDescendantIds,
} from "./ProblemsPage.folderHelpers";

function makeFolder(
  id: string,
  children: FolderNode[] = [],
  overrides: Partial<FolderNode> = {},
): FolderNode {
  return {
    id,
    name: id,
    parentId: null,
    problemCount: 0,
    children,
    createdAt: "",
    updatedAt: "",
    ...overrides,
  };
}

describe("flattenFolders", () => {
  it("flattens a nested folder tree with increasing depth", () => {
    const tree = [
      makeFolder("a", [makeFolder("b", [makeFolder("c")]), makeFolder("d")]),
      makeFolder("e"),
    ];

    expect(flattenFolders(tree)).toEqual([
      { ...tree[0], depth: 0 },
      { ...tree[0].children[0], depth: 1 },
      { ...tree[0].children[0].children[0], depth: 2 },
      { ...tree[0].children[1], depth: 1 },
      { ...tree[1], depth: 0 },
    ]);
  });

  it("returns an empty array for empty input", () => {
    expect(flattenFolders([])).toEqual([]);
  });

  it("handles a single root folder with no children", () => {
    const root = makeFolder("root");
    expect(flattenFolders([root])).toEqual([{ ...root, depth: 0 }]);
  });

  it("does not mutate the input folder nodes", () => {
    const child = makeFolder("child");
    const root = makeFolder("root", [child]);
    const flattened = flattenFolders([root]);
    expect(flattened[0]).not.toBe(root);
    expect(root.children).toHaveLength(1);
    expect(flattened[1]).not.toBe(child);
  });
});

describe("getDescendantIds", () => {
  it("returns all descendant ids across multiple levels", () => {
    const folder = makeFolder("root", [
      makeFolder("a", [makeFolder("b"), makeFolder("c")]),
      makeFolder("d"),
    ]);

    expect(getDescendantIds(folder)).toEqual(new Set(["a", "b", "c", "d"]));
  });

  it("returns an empty set for a leaf folder with no children", () => {
    expect(getDescendantIds(makeFolder("leaf"))).toEqual(new Set());
  });

  it("does not include the folder's own id", () => {
    const folder = makeFolder("root", [makeFolder("child")]);
    expect(getDescendantIds(folder).has("root")).toBe(false);
  });
});

describe("findFolder", () => {
  const tree = [
    makeFolder("a", [makeFolder("b", [makeFolder("c")])]),
    makeFolder("d"),
  ];

  it("finds a folder at the root level", () => {
    expect(findFolder(tree, "a")?.id).toBe("a");
  });

  it("finds a nested folder by id", () => {
    expect(findFolder(tree, "c")?.id).toBe("c");
  });

  it("returns undefined when the folder id is not present", () => {
    expect(findFolder(tree, "missing")).toBeUndefined();
  });

  it("returns undefined for an empty folder list", () => {
    expect(findFolder([], "anything")).toBeUndefined();
  });

  it("returns the folder node with its full shape", () => {
    const found = findFolder(tree, "b");
    expect(found).toMatchObject({ id: "b", children: [{ id: "c" }] });
  });
});

describe("buildAncestorMap", () => {
  const tree = [
    makeFolder("a", [makeFolder("b", [makeFolder("c")])]),
    makeFolder("d"),
  ];

  it("maps root folders to an empty ancestor list", () => {
    const map = buildAncestorMap(tree);
    expect(map.get("a")).toEqual([]);
    expect(map.get("d")).toEqual([]);
  });

  it("maps a nested folder to its ancestor chain", () => {
    const map = buildAncestorMap(tree);
    expect(map.get("c")).toEqual(["a", "b"]);
  });

  it("includes every folder in the tree", () => {
    const map = buildAncestorMap(tree);
    expect(Array.from(map.keys()).sort()).toEqual(["a", "b", "c", "d"]);
  });

  it("returns an empty map for an empty folder list", () => {
    expect(buildAncestorMap([]).size).toBe(0);
  });

  it("handles sibling roots independently", () => {
    const siblings = [makeFolder("root1", [makeFolder("child1")]), makeFolder("root2", [makeFolder("child2")])];
    const map = buildAncestorMap(siblings);
    expect(map.get("child1")).toEqual(["root1"]);
    expect(map.get("child2")).toEqual(["root2"]);
  });
});
