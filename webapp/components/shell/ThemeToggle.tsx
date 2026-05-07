"use client";
import { useEffect, useState } from "react";
import { applyTheme, readStoredTheme, type Theme } from "@/lib/theme";

interface Props {
  onThemeChange?: (t: Theme) => void;
}

export function ThemeToggle({ onThemeChange }: Props) {
  const [theme, setTheme] = useState<Theme>("light");

  useEffect(() => {
    setTheme(readStoredTheme());
  }, []);

  const flip = () => {
    const next: Theme = theme === "light" ? "dark" : "light";
    setTheme(next);
    applyTheme(next);
    onThemeChange?.(next);
  };

  return (
    <button
      className="theme-toggle"
      type="button"
      onClick={flip}
      title="Toggle bright / dark theme"
      aria-label="Toggle theme"
    >
      <svg className="sun" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" aria-hidden="true">
        <circle cx="8" cy="8" r="3" />
        <path d="M8 1v2M8 13v2M1 8h2M13 8h2M3.05 3.05l1.4 1.4M11.55 11.55l1.4 1.4M3.05 12.95l1.4-1.4M11.55 4.45l1.4-1.4" />
      </svg>
      <svg className="moon" width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
        <path d="M6 1.5A6.5 6.5 0 1 0 14.5 10 5 5 0 0 1 6 1.5z" />
      </svg>
    </button>
  );
}
