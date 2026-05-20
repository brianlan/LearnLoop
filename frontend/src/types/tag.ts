export interface TagItem {
  id: string;
  name: string;
  createdAt: string;
  problemCount: number;
}

export interface TagsResponse {
  items: TagItem[];
}

export interface TagResponse {
  tag: TagItem;
}
