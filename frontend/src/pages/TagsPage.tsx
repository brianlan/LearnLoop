import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { TagItem, TagsResponse, TagResponse } from "@/types/tag";

export function TagsPage() {
  const queryClient = useQueryClient();
  const [newTagName, setNewTagName] = useState("");
  const [editingTagId, setEditingTagId] = useState<string | null>(null);
  const [editingName, setEditingName] = useState("");
  const [deletingTag, setDeletingTag] = useState<TagItem | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { data: tagsData, isLoading } = useQuery({
    queryKey: ["tags"],
    queryFn: async () => {
      const response = await api.get<TagsResponse>("/tags");
      return response;
    },
  });

  const tags = tagsData?.items ?? [];

  const createMutation = useMutation({
    mutationFn: async (name: string) => {
      const response = await api.post<TagResponse>("/tags", { name });
      return response;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tags"] });
      setNewTagName("");
      setError(null);
    },
    onError: (err) => {
      if (err instanceof Error) {
        setError(err.message);
      }
    },
  });

  const renameMutation = useMutation({
    mutationFn: async ({ id, name }: { id: string; name: string }) => {
      const response = await api.patch<TagResponse>(`/tags/${id}`, { name });
      return response;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tags"] });
      queryClient.invalidateQueries({ queryKey: ["problems"] });
      setEditingTagId(null);
      setEditingName("");
      setError(null);
    },
    onError: (err) => {
      if (err instanceof Error) {
        setError(err.message);
      }
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      await api.delete<{ ok: boolean }>(`/tags/${id}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tags"] });
      queryClient.invalidateQueries({ queryKey: ["problems"] });
      setDeletingTag(null);
      setError(null);
    },
    onError: (err) => {
      if (err instanceof Error) {
        setError(err.message);
      }
    },
  });

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmedName = newTagName.trim();
    if (trimmedName) {
      createMutation.mutate(trimmedName);
    }
  };

  const startEdit = (tag: TagItem) => {
    setEditingTagId(tag.id);
    setEditingName(tag.name);
    setError(null);
  };

  const cancelEdit = () => {
    setEditingTagId(null);
    setEditingName("");
    setError(null);
  };

  const saveEdit = () => {
    const trimmedName = editingName.trim();
    if (trimmedName && editingTagId) {
      renameMutation.mutate({ id: editingTagId, name: trimmedName });
    }
  };

  const handleDelete = (tag: TagItem) => {
    setDeletingTag(tag);
  };

  const confirmDelete = () => {
    if (deletingTag) {
      deleteMutation.mutate(deletingTag.id);
    }
  };

  return (
    <main style={{ padding: "1rem", maxWidth: "800px", margin: "0 auto" }}>
      <h1>Tags</h1>

      <form
        onSubmit={handleCreate}
        style={{
          display: "flex",
          gap: "0.5rem",
          marginBottom: "1.5rem",
        }}
      >
        <input
          type="text"
          value={newTagName}
          onChange={(e) => setNewTagName(e.target.value)}
          placeholder="New tag name"
          data-testid="new-tag-input"
          style={{
            flex: 1,
            padding: "0.5rem 0.75rem",
            border: "1px solid #d1d5db",
            borderRadius: "6px",
            fontSize: "14px",
          }}
        />
        <button
          type="submit"
          disabled={!newTagName.trim() || createMutation.isPending}
          data-testid="add-tag-button"
          style={{
            padding: "0.5rem 1rem",
            backgroundColor: "#2563eb",
            color: "white",
            border: "none",
            borderRadius: "6px",
            cursor: newTagName.trim() ? "pointer" : "not-allowed",
            opacity: newTagName.trim() ? 1 : 0.5,
          }}
        >
          {createMutation.isPending ? "Adding..." : "Add Tag"}
        </button>
      </form>

      {error && (
        <div
          style={{
            padding: "0.75rem",
            backgroundColor: "#fef2f2",
            border: "1px solid #fecaca",
            borderRadius: "6px",
            color: "#dc2626",
            marginBottom: "1rem",
          }}
          data-testid="tag-error"
        >
          {error}
        </div>
      )}

      {isLoading ? (
        <div>Loading...</div>
      ) : tags.length === 0 ? (
        <div
          style={{
            textAlign: "center",
            padding: "2rem",
            color: "#6b7280",
          }}
          data-testid="empty-state"
        >
          <p>No tags yet.</p>
          <p style={{ fontSize: "0.875rem" }}>
            Create your first tag above, or add tags when editing problems.
          </p>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
          {tags.map((tag) => (
            <div
              key={tag.id}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "0.75rem",
                padding: "0.75rem 1rem",
                border: "1px solid #e5e7eb",
                borderRadius: "6px",
                backgroundColor: "#ffffff",
              }}
              data-testid={`tag-item-${tag.id}`}
            >
              {editingTagId === tag.id ? (
                <>
                  <input
                    type="text"
                    value={editingName}
                    onChange={(e) => setEditingName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        saveEdit();
                      }
                      if (e.key === "Escape") {
                        cancelEdit();
                      }
                    }}
                    autoFocus
                    data-testid={`edit-tag-input-${tag.id}`}
                    style={{
                      flex: 1,
                      padding: "0.25rem 0.5rem",
                      border: "1px solid #2563eb",
                      borderRadius: "4px",
                      fontSize: "14px",
                    }}
                  />
                  <button
                    type="button"
                    onClick={saveEdit}
                    disabled={renameMutation.isPending}
                    data-testid={`save-tag-button-${tag.id}`}
                    style={{
                      padding: "0.25rem 0.5rem",
                      backgroundColor: "#2563eb",
                      color: "white",
                      border: "none",
                      borderRadius: "4px",
                      cursor: "pointer",
                    }}
                  >
                    Save
                  </button>
                  <button
                    type="button"
                    onClick={cancelEdit}
                    disabled={renameMutation.isPending}
                    data-testid={`cancel-edit-button-${tag.id}`}
                    style={{
                      padding: "0.25rem 0.5rem",
                      backgroundColor: "#f3f4f6",
                      color: "#374151",
                      border: "1px solid #d1d5db",
                      borderRadius: "4px",
                      cursor: "pointer",
                    }}
                  >
                    Cancel
                  </button>
                </>
              ) : (
                <>
                  <span style={{ flex: 1, fontSize: "14px" }}>{tag.name}</span>
                  <span
                    style={{
                      padding: "0.125rem 0.5rem",
                      backgroundColor: "#f3f4f6",
                      borderRadius: "4px",
                      fontSize: "0.75rem",
                      color: "#6b7280",
                    }}
                  >
                    {tag.problemCount} problem{tag.problemCount !== 1 ? "s" : ""}
                  </span>
                  <button
                    type="button"
                    onClick={() => startEdit(tag)}
                    data-testid={`edit-tag-button-${tag.id}`}
                    style={{
                      padding: "0.25rem 0.5rem",
                      backgroundColor: "#f3f4f6",
                      border: "1px solid #d1d5db",
                      borderRadius: "4px",
                      cursor: "pointer",
                      fontSize: "0.875rem",
                    }}
                  >
                    Rename
                  </button>
                  <button
                    type="button"
                    onClick={() => handleDelete(tag)}
                    data-testid={`delete-tag-button-${tag.id}`}
                    style={{
                      padding: "0.25rem 0.5rem",
                      backgroundColor: "#fef2f2",
                      color: "#dc2626",
                      border: "1px solid #fecaca",
                      borderRadius: "4px",
                      cursor: "pointer",
                      fontSize: "0.875rem",
                    }}
                  >
                    Delete
                  </button>
                </>
              )}
            </div>
          ))}
        </div>
      )}

      {deletingTag && (
        <div
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            backgroundColor: "rgba(0, 0, 0, 0.5)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 50,
          }}
          data-testid="delete-confirm-modal"
        >
          <div
            style={{
              backgroundColor: "white",
              padding: "1.5rem",
              borderRadius: "8px",
              maxWidth: "400px",
              width: "100%",
            }}
          >
            <h2 style={{ marginTop: 0, marginBottom: "0.75rem" }}>
              Delete Tag
            </h2>
            <p style={{ marginBottom: "1rem" }}>
              Are you sure you want to delete "{deletingTag.name}"? This will
              remove the tag from {deletingTag.problemCount} problem
              {deletingTag.problemCount !== 1 ? "s" : ""}. This cannot be undone.
            </p>
            <div style={{ display: "flex", gap: "0.5rem", justifyContent: "flex-end" }}>
              <button
                type="button"
                onClick={() => setDeletingTag(null)}
                disabled={deleteMutation.isPending}
                style={{
                  padding: "0.5rem 1rem",
                  backgroundColor: "#f3f4f6",
                  border: "1px solid #d1d5db",
                  borderRadius: "6px",
                  cursor: "pointer",
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={confirmDelete}
                disabled={deleteMutation.isPending}
                data-testid="confirm-delete-button"
                style={{
                  padding: "0.5rem 1rem",
                  backgroundColor: "#dc2626",
                  color: "white",
                  border: "none",
                  borderRadius: "6px",
                  cursor: "pointer",
                }}
              >
                {deleteMutation.isPending ? "Deleting..." : "Delete"}
              </button>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
