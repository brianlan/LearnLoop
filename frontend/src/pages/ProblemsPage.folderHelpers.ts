import type { FolderNode } from "@/types/folder";

export function flattenFolders(
  folders: FolderNode[],
  depth = 0,
): Array<FolderNode & { depth: number }> {
  return folders.flatMap((folder) => [
    { ...folder, depth },
    ...flattenFolders(folder.children, depth + 1),
  ]);
}

export function getDescendantIds(folder: FolderNode): Set<string> {
  const ids = new Set<string>();
  for (const child of folder.children) {
    ids.add(child.id);
    for (const id of getDescendantIds(child)) ids.add(id);
  }
  return ids;
}

export function findFolder(
  folders: FolderNode[],
  folderId: string,
): FolderNode | undefined {
  for (const folder of folders) {
    if (folder.id === folderId) return folder;
    const found = findFolder(folder.children, folderId);
    if (found) return found;
  }
  return undefined;
}

export function buildAncestorMap(folders: FolderNode[]): Map<string, string[]> {
  const map = new Map<string, string[]>();
  const visit = (folder: FolderNode, ancestors: string[]) => {
    map.set(folder.id, ancestors);
    folder.children.forEach((child) => visit(child, [...ancestors, folder.id]));
  };
  folders.forEach((folder) => visit(folder, []));
  return map;
}
