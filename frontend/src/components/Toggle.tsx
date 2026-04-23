interface ToggleProps {
  on: boolean;
  onChange: (v: boolean) => void;
  label?: string;
  disabled?: boolean;
  'aria-label'?: string;
}

export function Toggle({ on, onChange, label, disabled, 'aria-label': ariaLabel }: ToggleProps) {
  return (
    <label className="toggle" style={disabled ? { opacity: 0.5, cursor: 'not-allowed' } : {}}>
      <div
        role="switch"
        aria-checked={on}
        aria-label={ariaLabel ?? label}
        tabIndex={0}
        onClick={() => !disabled && onChange(!on)}
        onKeyDown={(e) => { if ((e.key === 'Enter' || e.key === ' ') && !disabled) onChange(!on); }}
        className={`toggle-track${on ? ' on' : ''}`}
      >
        <div className="toggle-thumb" />
      </div>
      {label && <span className="toggle-label">{label}</span>}
    </label>
  );
}
