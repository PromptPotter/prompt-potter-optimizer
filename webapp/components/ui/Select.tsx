import type { ReactNode, SelectHTMLAttributes } from "react";

export interface SelectOption<T extends string> {
  value: T;
  label: ReactNode;
  disabled?: boolean;
}

type Owned = "value" | "defaultValue" | "onChange" | "children";

// The one dropdown: typed values in, the picked value out. `placeholder` is a disabled first option
// shown while `value` is "" (nothing picked yet).
export function Select<T extends string>({
  value,
  options,
  onChange,
  placeholder,
  ...rest
}: {
  value: T | "";
  options: readonly SelectOption<T>[];
  onChange: (value: T) => void;
  placeholder?: string;
} & Omit<SelectHTMLAttributes<HTMLSelectElement>, Owned>) {
  return (
    <select {...rest} value={value} onChange={(e) => onChange(e.target.value as T)}>
      {placeholder !== undefined && value === "" ? (
        <option value="" disabled>
          {placeholder}
        </option>
      ) : null}
      {options.map((o) => (
        <option key={o.value} value={o.value} disabled={o.disabled}>
          {o.label}
        </option>
      ))}
    </select>
  );
}
