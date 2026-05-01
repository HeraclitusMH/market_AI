import { useEffect, useState } from 'react';

export type ThemeName = 'matrix' | 'dream';

const STORAGE_KEY = 'market-ai-theme';
const PARTICLE_COUNT = 28;

function readStoredTheme(): ThemeName {
  if (typeof window === 'undefined') return 'matrix';
  return window.localStorage.getItem(STORAGE_KEY) === 'dream' ? 'dream' : 'matrix';
}

function applyTheme(theme: ThemeName) {
  const root = document.documentElement;
  if (theme === 'dream') {
    root.dataset.theme = 'dream';
  } else {
    root.removeAttribute('data-theme');
  }
}

function removeParticles() {
  document.querySelector('.dream-particles')?.remove();
}

function ensureParticles() {
  if (document.querySelector('.dream-particles')) return;

  const layer = document.createElement('div');
  layer.className = 'dream-particles';
  layer.setAttribute('aria-hidden', 'true');

  for (let i = 0; i < PARTICLE_COUNT; i += 1) {
    const mote = document.createElement('span');
    mote.style.setProperty('--x', `${Math.round(Math.random() * 100)}vw`);
    mote.style.setProperty('--size', `${1 + Math.random() * 3}px`);
    mote.style.setProperty('--duration', `${12 + Math.random() * 14}s`);
    mote.style.setProperty('--delay', `${Math.random() * -26}s`);
    mote.style.setProperty('--drift', `${Math.round((Math.random() - 0.5) * 90)}px`);
    layer.appendChild(mote);
  }

  document.body.appendChild(layer);
}

export function useTheme() {
  const [theme, setThemeState] = useState<ThemeName>(readStoredTheme);

  useEffect(() => {
    applyTheme(theme);
    window.localStorage.setItem(STORAGE_KEY, theme);

    if (theme === 'dream') {
      ensureParticles();
    } else {
      removeParticles();
    }

    return () => {
      removeParticles();
    };
  }, [theme]);

  function setTheme(next: ThemeName) {
    setThemeState(next);
  }

  return { theme, setTheme };
}
