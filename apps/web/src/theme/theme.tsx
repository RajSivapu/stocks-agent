import { createContext, type PropsWithChildren, useContext, useEffect, useMemo, useState } from "react";

export type ThemeMode = "system" | "light" | "dark";
const STORAGE_KEY = "personal-stock-agent-theme";

interface ThemeContextValue {
  mode: ThemeMode;
  setMode(mode: ThemeMode): void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

function storedMode(): ThemeMode {
  const value = window.localStorage.getItem(STORAGE_KEY);
  return value === "light" || value === "dark" || value === "system" ? value : "system";
}

export function ThemeProvider({ children }: PropsWithChildren) {
  const [mode, setModeState] = useState<ThemeMode>(storedMode);
  useEffect(() => {
    document.documentElement.dataset.theme = mode;
  }, [mode]);
  const value = useMemo(() => ({
    mode,
    setMode: (next: ThemeMode) => {
      if (!(["system", "light", "dark"] as const).includes(next)) return;
      document.documentElement.dataset.theme = next;
      window.localStorage.setItem(STORAGE_KEY, next);
      setModeState(next);
    },
  }), [mode]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const value = useContext(ThemeContext);
  if (!value) throw new Error("useTheme must be used within ThemeProvider");
  return value;
}
