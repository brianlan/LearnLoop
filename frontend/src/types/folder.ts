export interface FolderNode {
  id: string;
  name: string;
  parentId: string | null;
  problemCount: number;
  children: FolderNode[];
  createdAt: string;
  updatedAt: string;
}

export interface FolderTreeResponse {
  allProblemsCount: number;
  unfiledCount: number;
  items: FolderNode[];
}
