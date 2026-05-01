import { useTheme, type ThemeName } from '@/hooks/useTheme';

const OPTIONS: Array<{ value: ThemeName; label: string }> = [
  { value: 'matrix', label: 'Matrix' },
  { value: 'dream', label: 'Dream' },
];

export function ThemeSwitch() {
  const { theme, setTheme } = useTheme();

  return (
    <div className="theme-switch" role="group" aria-label="Theme">
      {OPTIONS.map((option) => (
        <button
          key={option.value}
          type="button"
          className={`theme-switch-item${theme === option.value ? ' active' : ''}`}
          onClick={() => setTheme(option.value)}
          aria-pressed={theme === option.value}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
