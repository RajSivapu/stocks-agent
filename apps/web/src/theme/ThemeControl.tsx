import { useTheme, type ThemeMode } from "./theme";

export function ThemeControl() {
  const { mode, setMode } = useTheme();
  return (
    <fieldset className="theme-control">
      <legend>Appearance</legend>
      {(["system", "light", "dark"] as ThemeMode[]).map((value) => (
        <label key={value}>
          <input
            type="radio"
            name="theme"
            value={value}
            checked={mode === value}
            onChange={() => setMode(value)}
          />
          {value[0].toUpperCase() + value.slice(1)}
        </label>
      ))}
    </fieldset>
  );
}
