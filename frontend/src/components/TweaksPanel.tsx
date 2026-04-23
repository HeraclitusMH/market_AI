import { useState } from 'react';
import { Settings } from 'lucide-react';
import { SegmentedControl } from './SegmentedControl';
import { type Tweaks, loadTweaks, saveTweaks } from '@/hooks/useTweaks';

const ACCENT_SWATCHES = [
  { hue: 198, label: 'Cyan' },
  { hue: 240, label: 'Blue' },
  { hue: 270, label: 'Purple' },
  { hue: 150, label: 'Green' },
  { hue: 30, label: 'Orange' },
];

const DENSITY_OPTIONS = [
  { value: 'dense', label: 'Dense' },
  { value: 'balanced', label: 'Balanced' },
  { value: 'airy', label: 'Airy' },
];

export function TweaksPanel() {
  const [open, setOpen] = useState(false);
  const [tweaks, setTweaks] = useState<Tweaks>(loadTweaks);

  function update(patch: Partial<Tweaks>) {
    const next = { ...tweaks, ...patch };
    setTweaks(next);
    saveTweaks(next);
  }

  if (!open) {
    return (
      <button
        className="tweaks-toggle-btn"
        onClick={() => setOpen(true)}
        aria-label="Open tweaks panel"
        title="Tweaks"
      >
        <Settings size={15} />
      </button>
    );
  }

  return (
    <div className="tweaks-panel" role="dialog" aria-label="Tweaks">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <span className="tweaks-panel-title">Tweaks</span>
        <button
          className="btn ghost sm"
          onClick={() => setOpen(false)}
          aria-label="Close tweaks"
          style={{ padding: '3px 8px' }}
        >
          ✕
        </button>
      </div>

      <div className="tweaks-section">
        <div className="tweaks-section-label">Accent colour</div>
        <div className="accent-swatches">
          {ACCENT_SWATCHES.map((s) => (
            <button
              key={s.hue}
              className={`accent-swatch${tweaks.accentHue === s.hue ? ' selected' : ''}`}
              style={{ background: `oklch(0.78 0.14 ${s.hue})` }}
              onClick={() => update({ accentHue: s.hue })}
              aria-label={s.label}
              title={s.label}
            />
          ))}
        </div>
      </div>

      <div className="tweaks-section">
        <div className="tweaks-section-label">Density</div>
        <SegmentedControl
          options={DENSITY_OPTIONS}
          value={tweaks.density}
          onChange={(v) => update({ density: v as Tweaks['density'] })}
          aria-label="Layout density"
        />
      </div>
    </div>
  );
}
