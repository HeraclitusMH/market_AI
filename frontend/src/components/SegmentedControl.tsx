interface SegOption { value: string; label: string; }

interface SegmentedControlProps {
  options: SegOption[];
  value: string;
  onChange: (v: string) => void;
  'aria-label'?: string;
}

export function SegmentedControl({ options, value, onChange, 'aria-label': ariaLabel }: SegmentedControlProps) {
  return (
    <div className="seg-ctrl" role="group" aria-label={ariaLabel}>
      {options.map((opt) => (
        <button
          key={opt.value}
          className={`seg-ctrl-item${value === opt.value ? ' active' : ''}`}
          onClick={() => onChange(opt.value)}
          aria-pressed={value === opt.value}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
