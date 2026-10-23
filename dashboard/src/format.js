// Number, date and hash formatting shared by the views and chart tooltips.

/** Format a number with ``digits`` decimals; ``signed`` adds a plus sign. */
export function formatNumber(value, digits = 2, signed = false) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '–';
  const text = Number(value).toFixed(digits);
  return signed && Number(value) > 0 ? `+${text}` : text;
}

/** Return the first ten characters of a commit hash. */
export function shortSha(sha) {
  return sha ? sha.slice(0, 10) : '';
}

/** Format an ISO 8601 UTC time as "2026-09-10 13:00 UTC". */
export function utcDate(iso) {
  if (!iso) return '–';
  return `${iso.slice(0, 10)} ${iso.slice(11, 16)} UTC`;
}

/** Format a number of seconds as m:ss or h:mm:ss. */
export function duration(seconds) {
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`;
}
