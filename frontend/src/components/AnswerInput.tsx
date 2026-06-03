export function extractOptionKey(option: string): string {
  const match = option.trim().match(/^([A-Za-z]|\d+)\s*[.):\-]?(?:\s|$)/);
  return (match?.[1] ?? option).trim();
}

export function parseOptions(text: string): string[] {
  const lines = text.split("\n");
  const lettered: string[] = [];
  const numeric: string[] = [];
  for (const line of lines) {
    const trimmed = line.trim();
    if (/^[A-Z][.):\s]/.test(trimmed)) {
      lettered.push(trimmed);
    } else if (/^\d+[.):\s]/.test(trimmed)) {
      numeric.push(trimmed);
    }
  }
  if (lettered.length > 0) return lettered;
  if (numeric.length > 0) return numeric;
  return [];
}

interface SingleChoiceInputProps {
  value: string;
  onChange: (value: string) => void;
  options: string[];
  onBlur?: () => void;
  disabled?: boolean;
}

export function SingleChoiceInput({ value, onChange, options, onBlur, disabled }: SingleChoiceInputProps) {
  if (options.length === 0) {
    return (
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onBlur={onBlur}
        disabled={disabled}
        style={{ width: "100%", padding: "0.5rem", fontSize: "1rem", border: "1px solid var(--color-border)", borderRadius: "0.25rem" }}
      />
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
      {options.map((option) => {
        const optionValue = extractOptionKey(option);
        return (
          <label
            key={option}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "0.5rem",
              padding: "0.5rem",
              cursor: disabled ? "default" : "pointer",
              borderRadius: "0.25rem",
            }}
          >
            <input
              type="radio"
              name="single-choice"
              value={optionValue}
              checked={value === optionValue || value === option}
              onChange={() => onChange(optionValue)}
              onBlur={onBlur}
              disabled={disabled}
            />
            <span>{option}</span>
          </label>
        );
      })}
    </div>
  );
}

interface MultiChoiceInputProps {
  value: string;
  onChange: (value: string) => void;
  options: string[];
  onBlur?: () => void;
  disabled?: boolean;
}

export function MultiChoiceInput({ value, onChange, options, onBlur, disabled }: MultiChoiceInputProps) {
  const selectedValues = value ? value.split(",").map((v) => v.trim()).filter(Boolean) : [];

  const handleToggle = (optionValue: string) => {
    const newValues = selectedValues.includes(optionValue)
      ? selectedValues.filter((v) => v !== optionValue)
      : [...selectedValues, optionValue];
    onChange(newValues.join(", "));
  };

  if (options.length === 0) {
    return (
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onBlur={onBlur}
        disabled={disabled}
        placeholder="Enter options separated by commas"
        style={{ width: "100%", padding: "0.5rem", fontSize: "1rem", border: "1px solid var(--color-border)", borderRadius: "0.25rem" }}
      />
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
      {options.map((option) => {
        const optionValue = extractOptionKey(option);
        return (
          <label
            key={option}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "0.5rem",
              padding: "0.5rem",
              cursor: disabled ? "default" : "pointer",
              borderRadius: "0.25rem",
            }}
          >
            <input
              type="checkbox"
              checked={selectedValues.includes(optionValue) || selectedValues.includes(option)}
              onChange={() => handleToggle(optionValue)}
              onBlur={onBlur}
              disabled={disabled}
            />
            <span>{option}</span>
          </label>
        );
      })}
    </div>
  );
}

interface TextInputProps {
  value: string;
  onChange: (value: string) => void;
  multiline?: boolean;
  onBlur?: () => void;
  disabled?: boolean;
}

export function TextInput({ value, onChange, multiline, onBlur, disabled }: TextInputProps) {
  if (multiline) {
    return (
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onBlur={onBlur}
        disabled={disabled}
        style={{
          width: "100%",
          padding: "0.5rem",
          fontSize: "1rem",
          border: "1px solid var(--color-border)",
          borderRadius: "0.25rem",
          minHeight: "120px",
          resize: "vertical",
        }}
      />
    );
  }
  return (
    <input
      type="text"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      onBlur={onBlur}
      disabled={disabled}
      style={{ width: "100%", padding: "0.5rem", fontSize: "1rem", border: "1px solid var(--color-border)", borderRadius: "0.25rem" }}
    />
  );
}

interface AnswerInputProps {
  problemType: string;
  value: string;
  onChange: (value: string) => void;
  onBlur?: () => void;
  options: string[];
  disabled?: boolean;
}

export function AnswerInput({
  problemType,
  value,
  onChange,
  onBlur,
  options,
  disabled,
}: AnswerInputProps) {
  switch (problemType) {
    case "single-choice":
      return <SingleChoiceInput value={value} onChange={onChange} onBlur={onBlur} options={options} disabled={disabled} />;
    case "multi-choice":
      return <MultiChoiceInput value={value} onChange={onChange} onBlur={onBlur} options={options} disabled={disabled} />;
    case "short-answer":
      return <TextInput value={value} onChange={onChange} onBlur={onBlur} multiline disabled={disabled} />;
    case "fill-in-the-blank":
    default:
      return <TextInput value={value} onChange={onChange} onBlur={onBlur} disabled={disabled} />;
  }
}
