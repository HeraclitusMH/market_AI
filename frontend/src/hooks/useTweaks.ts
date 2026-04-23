import { useEffect } from 'react';

export interface Tweaks {
  accentHue: number;
  density: 'dense' | 'balanced' | 'airy';
}

export const DENSITY_MAP = { dense: 0.75, balanced: 1, airy: 1.25 } as const;
export const STORAGE_KEY = 'mai_tweaks';
export const DEFAULT_TWEAKS: Tweaks = { accentHue: 198, density: 'balanced' };

export function loadTweaks(): Tweaks {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    return saved ? { ...DEFAULT_TWEAKS, ...JSON.parse(saved) } : DEFAULT_TWEAKS;
  } catch {
    return DEFAULT_TWEAKS;
  }
}

export function applyTweaks(tweaks: Tweaks) {
  document.documentElement.style.setProperty('--accent-h', String(tweaks.accentHue));
  document.documentElement.style.setProperty('--density', String(DENSITY_MAP[tweaks.density]));
}

export function saveTweaks(tweaks: Tweaks) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(tweaks));
  applyTweaks(tweaks);
}

export function useTweaks() {
  useEffect(() => {
    applyTweaks(loadTweaks());
  }, []);
}
