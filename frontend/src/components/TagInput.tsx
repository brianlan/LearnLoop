import { useState, useCallback, useMemo, useRef, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";

interface TagsResponse {
  items: string[];
}

interface TagInputProps {
  value: string[];
  onChange: (tags: string[]) => void;
  placeholder?: string;
}

export function TagInput({ value, onChange, placeholder = "Add a tag..." }: TagInputProps) {
  const [inputValue, setInputValue] = useState("");
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [selectedSuggestionIndex, setSelectedSuggestionIndex] = useState(-1);
  const inputRef = useRef<HTMLInputElement>(null);

  const { data: existingTags = [] } = useQuery({
    queryKey: ["tags"],
    queryFn: async () => {
      const data = await api.get<TagsResponse>("/problems/tags");
      return data.items;
    },
  });

  const suggestions = useMemo(() => {
    if (!inputValue.trim()) return [];
    const lowerInput = inputValue.toLowerCase();
    return existingTags.filter(
      (tag) =>
        !value.includes(tag) && tag.toLowerCase().includes(lowerInput)
    );
  }, [existingTags, inputValue, value]);

  const addTag = useCallback(
    (tag: string) => {
      const trimmedTag = tag.trim();
      if (trimmedTag && !value.includes(trimmedTag)) {
        onChange([...value, trimmedTag]);
      }
      setInputValue("");
      setShowSuggestions(false);
      setSelectedSuggestionIndex(-1);
    },
    [value, onChange]
  );

  const removeTag = useCallback(
    (tagToRemove: string) => {
      onChange(value.filter((tag) => tag !== tagToRemove));
    },
    [value, onChange]
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Enter" || e.key === ",") {
        e.preventDefault();
        if (selectedSuggestionIndex >= 0 && suggestions[selectedSuggestionIndex]) {
          addTag(suggestions[selectedSuggestionIndex]);
        } else {
          addTag(inputValue);
        }
      } else if (e.key === "Backspace" && !inputValue) {
        e.preventDefault();
        if (value.length > 0) {
          removeTag(value[value.length - 1]);
        }
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        setShowSuggestions(true);
        setSelectedSuggestionIndex((prev) =>
          prev < suggestions.length - 1 ? prev + 1 : 0
        );
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setShowSuggestions(true);
        setSelectedSuggestionIndex((prev) =>
          prev > 0 ? prev - 1 : suggestions.length - 1
        );
      } else if (e.key === "Escape") {
        setShowSuggestions(false);
        setSelectedSuggestionIndex(-1);
      }
    },
    [inputValue, suggestions, addTag, removeTag, value, selectedSuggestionIndex]
  );

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (
        inputRef.current &&
        !inputRef.current.contains(e.target as Node)
      ) {
        setShowSuggestions(false);
      }
    };

    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  return (
    <div style={{ position: "relative" }} ref={inputRef}>
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: "0.5rem",
          padding: "0.5rem",
          border: "1px solid #d1d5db",
          borderRadius: "0.5rem",
          backgroundColor: "white",
          minHeight: "2.5rem",
          alignItems: "center",
        }}
      >
        {value.map((tag) => (
          <span
            key={tag}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "0.25rem",
              padding: "0.25rem 0.5rem",
              backgroundColor: "#eff6ff",
              color: "#1d4ed8",
              borderRadius: "9999px",
              fontSize: "0.875rem",
            }}
          >
            {tag}
            <button
              type="button"
              onClick={() => removeTag(tag)}
              style={{
                background: "none",
                border: "none",
                cursor: "pointer",
                fontSize: "1rem",
                padding: 0,
                color: "#60a5fa",
                display: "flex",
                alignItems: "center",
              }}
              aria-label={`Remove ${tag}`}
            >
              ×
            </button>
          </span>
        ))}
        <input
          type="text"
          value={inputValue}
          onChange={(e) => {
            setInputValue(e.target.value);
            setShowSuggestions(true);
            setSelectedSuggestionIndex(-1);
          }}
          onKeyDown={handleKeyDown}
          onFocus={() => setShowSuggestions(true)}
          placeholder={value.length === 0 ? placeholder : ""}
          style={{
            flex: 1,
            minWidth: "8rem",
            border: "none",
            outline: "none",
            fontSize: "0.875rem",
            padding: "0.25rem",
          }}
          data-testid="tag-input-field"
        />
      </div>

      {showSuggestions && suggestions.length > 0 && (
        <div
          style={{
            position: "absolute",
            top: "100%",
            left: 0,
            right: 0,
            marginTop: "0.25rem",
            backgroundColor: "white",
            border: "1px solid #e5e7eb",
            borderRadius: "0.5rem",
            boxShadow: "0 4px 6px -1px rgb(0 0 0 / 0.1)",
            zIndex: 10,
            maxHeight: "12rem",
            overflowY: "auto",
          }}
        >
          {suggestions.map((suggestion, index) => (
            <button
              key={suggestion}
              type="button"
              onClick={() => addTag(suggestion)}
              style={{
                width: "100%",
                textAlign: "left",
                padding: "0.5rem 0.75rem",
                border: "none",
                background:
                  index === selectedSuggestionIndex
                    ? "#eff6ff"
                    : "white",
                cursor: "pointer",
                fontSize: "0.875rem",
              }}
            >
              {suggestion}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
