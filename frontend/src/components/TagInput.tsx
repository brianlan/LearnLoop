import { KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";

export interface TagInputProps {
  tags: string[];
  onChange: (tags: string[]) => void;
  suggestions?: string[];
  placeholder?: string;
  disabled?: boolean;
  label?: string;
  testId?: string;
}

export function TagInput({
  tags,
  onChange,
  suggestions = [],
  placeholder,
  disabled = false,
  label,
  testId = "tag-input",
}: TagInputProps) {
  const [inputValue, setInputValue] = useState("");
  const [isOpen, setIsOpen] = useState(false);
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const [hoveredRemoveTag, setHoveredRemoveTag] = useState<string | null>(null);
  const wrapperRef = useRef<HTMLDivElement | null>(null);

  const filteredSuggestions = useMemo(() => {
    const trimmedInput = inputValue.trim().toLowerCase();

    if (!trimmedInput) {
      return [];
    }

    return suggestions.filter((suggestion) => {
      const normalizedSuggestion = suggestion.toLowerCase();
      return normalizedSuggestion.startsWith(trimmedInput) && !tags.includes(suggestion);
    });
  }, [inputValue, suggestions, tags]);

  useEffect(() => {
    if (!isOpen) {
      return;
    }

    setHighlightedIndex((currentIndex) => {
      if (filteredSuggestions.length === 0) {
        return 0;
      }

      return Math.min(currentIndex, filteredSuggestions.length - 1);
    });
  }, [filteredSuggestions, isOpen]);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (wrapperRef.current && !wrapperRef.current.contains(event.target as Node)) {
        setIsOpen(false);
        setHighlightedIndex(0);
      }
    }

    document.addEventListener("mousedown", handleClickOutside);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, []);

  const closeSuggestions = () => {
    setIsOpen(false);
    setHighlightedIndex(0);
  };

  const addTag = (value: string) => {
    const segments = value.split(",").map((s) => s.trim()).filter((s) => s.length > 0);

    if (segments.length === 0) {
      setInputValue("");
      closeSuggestions();
      return;
    }

    const uniqueSegments = segments.filter((s) => !tags.includes(s));
    const newTags = uniqueSegments.filter(
      (s, index) => uniqueSegments.indexOf(s) === index,
    );

    if (newTags.length > 0) {
      onChange([...tags, ...newTags]);
    }

    setInputValue("");
    closeSuggestions();
  };

  const removeTag = (tagToRemove: string) => {
    onChange(tags.filter((tag) => tag !== tagToRemove));
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (disabled) {
      return;
    }

    if (event.key === "ArrowDown") {
      if (filteredSuggestions.length > 0) {
        event.preventDefault();
        setIsOpen(true);
        setHighlightedIndex((currentIndex) => (currentIndex + 1) % filteredSuggestions.length);
      }
      return;
    }

    if (event.key === "ArrowUp") {
      if (filteredSuggestions.length > 0) {
        event.preventDefault();
        setIsOpen(true);
        setHighlightedIndex((currentIndex) =>
          currentIndex === 0 ? filteredSuggestions.length - 1 : currentIndex - 1,
        );
      }
      return;
    }

    if (event.key === "Enter") {
      event.preventDefault();

      if (isOpen && filteredSuggestions[highlightedIndex]) {
        addTag(filteredSuggestions[highlightedIndex]);
        return;
      }

      addTag(inputValue);
      return;
    }

    if (event.key === "Escape") {
      event.preventDefault();
      setInputValue("");
      closeSuggestions();
      return;
    }

    if (event.key === "Backspace" && inputValue === "" && tags.length > 0) {
      event.preventDefault();
      onChange(tags.slice(0, -1));
    }
  };

  const showSuggestions = isOpen && filteredSuggestions.length > 0 && !disabled;

  return (
    <div style={{ marginBottom: "24px", position: "relative" }} ref={wrapperRef} data-testid={testId}>
      {label ? (
        <label
          style={{
            display: "block",
            marginBottom: "6px",
            fontSize: "14px",
            fontWeight: 500,
            color: "var(--color-text)",
          }}
        >
          {label}
        </label>
      ) : null}

      <div
        style={{
          width: "100%",
          border: "1px solid var(--color-border)",
          borderRadius: "6px",
          padding: "6px 8px",
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          gap: "4px",
          boxSizing: "border-box",
          backgroundColor: disabled ? "var(--color-surface-muted)" : "var(--color-surface)",
        }}
      >
        {tags.map((tag) => (
          <span
            key={tag}
            style={{
              padding: "4px 8px",
              backgroundColor: "var(--color-tag-bg)",
              borderRadius: "4px",
              fontSize: "13px",
              display: "inline-flex",
              alignItems: "center",
              gap: "4px",
              margin: "2px",
            }}
            data-testid={`${testId}-tag-${tag}`}
          >
            <span>{tag}</span>
            <button
              type="button"
              onClick={() => removeTag(tag)}
              disabled={disabled}
              aria-label={`Remove ${tag}`}
              data-testid={`${testId}-remove-${tag}`}
              onMouseEnter={() => setHoveredRemoveTag(tag)}
              onMouseLeave={() => setHoveredRemoveTag((currentTag) => (currentTag === tag ? null : currentTag))}
              style={{
                border: "none",
                background: "transparent",
                cursor: disabled ? "not-allowed" : "pointer",
                color: hoveredRemoveTag === tag ? "var(--color-danger)" : "var(--color-text-muted)",
                fontSize: "16px",
                lineHeight: 1,
                padding: "0 2px",
              }}
            >
              ×
            </button>
          </span>
        ))}

        <input
          type="text"
          role="combobox"
          aria-expanded={showSuggestions}
          aria-haspopup="listbox"
          aria-autocomplete="list"
          aria-controls={showSuggestions ? `${testId}-suggestions` : undefined}
          aria-activedescendant={
            showSuggestions && filteredSuggestions[highlightedIndex]
              ? `${testId}-suggestion-${filteredSuggestions[highlightedIndex]}`
              : undefined
          }
          value={inputValue}
          disabled={disabled}
          placeholder={placeholder}
          onChange={(event) => {
            const nextValue = event.target.value;
            setInputValue(nextValue);
            setIsOpen(nextValue.trim().length > 0);
            setHighlightedIndex(0);
          }}
          onFocus={() => {
            if (inputValue.trim()) {
              setIsOpen(true);
            }
          }}
          onKeyDown={handleKeyDown}
          data-testid={`${testId}-field`}
          style={{
            border: "none",
            outline: "none",
            flex: 1,
            minWidth: "120px",
            fontSize: "14px",
            padding: "4px 0",
            fontFamily: "inherit",
            backgroundColor: "transparent",
          }}
        />
      </div>

      {showSuggestions ? (
        <div
          role="listbox"
          style={{
            position: "absolute",
            top: "100%",
            left: 0,
            right: 0,
            marginTop: "4px",
            backgroundColor: "var(--color-surface)",
            border: "1px solid var(--color-border)",
            borderRadius: "6px",
            boxShadow: "0 4px 6px rgba(0,0,0,0.1)",
            maxHeight: "200px",
            overflowY: "auto",
            zIndex: 10,
          }}
          data-testid={`${testId}-suggestions`}
        >
          {filteredSuggestions.map((suggestion, index) => (
            <div
              key={suggestion}
              role="option"
              aria-selected={index === highlightedIndex}
              onMouseDown={(event) => {
                event.preventDefault();
              }}
              onClick={() => addTag(suggestion)}
              data-testid={`${testId}-suggestion-${suggestion}`}
              id={`${testId}-suggestion-${suggestion}`}
              style={{
                width: "100%",
                border: "none",
                textAlign: "left",
                backgroundColor: index === highlightedIndex ? "var(--color-primary-bg)" : "var(--color-surface)",
                padding: "8px 12px",
                cursor: "pointer",
                fontSize: "14px",
              }}
            >
              {suggestion}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
