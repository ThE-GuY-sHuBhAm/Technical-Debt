// Chart colours for light and dark mode. Chart.js draws on a canvas, so it cannot read
// CSS variables; the same values are mirrored in styles.css for the page itself.
// Series colours follow a fixed categorical order (never cycled); the classification
// markers use the reserved status colours together with a marker shape, so colour is
// never the only cue (NFR-10).
import { useEffect, useState } from 'react';

const LIGHT = {
  surface: '#fcfcfb',
  ink: '#0b0b0b',
  inkSecondary: '#52514e',
  muted: '#898781',
  grid: '#e1e0d9',
  axis: '#c3c2b7',
  series: ['#2a78d6', '#eb6834', '#1baf7a', '#eda100'],
  good: '#0ca30c',
  critical: '#d03b3b',
  zone: 'rgba(208, 59, 59, 0.12)',
};

const DARK = {
  surface: '#1a1a19',
  ink: '#f0efec',
  inkSecondary: '#c3c2b7',
  muted: '#898781',
  grid: '#2c2c2a',
  axis: '#383835',
  series: ['#3987e5', '#d95926', '#199e70', '#c98500'],
  good: '#0ca30c',
  critical: '#d03b3b',
  zone: 'rgba(208, 59, 59, 0.22)',
};

const QUERY = '(prefers-color-scheme: dark)';

function dark() {
  return typeof window !== 'undefined' && window.matchMedia
    && window.matchMedia(QUERY).matches;
}

/** Return the chart colours for the current colour scheme, following its changes. */
export function useTheme() {
  const [isDark, setDark] = useState(dark);
  useEffect(() => {
    if (!window.matchMedia) return undefined;
    const media = window.matchMedia(QUERY);
    const update = () => setDark(media.matches);
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  return isDark ? DARK : LIGHT;
}
