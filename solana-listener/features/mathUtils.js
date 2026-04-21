/** Safely divide num by denom. Returns fallback when denom is 0 or non-finite. */
export const safeDiv = (num, denom, fallback = 0) =>
  isFinite(denom) && denom !== 0 ? num / denom : fallback;

/** Clamp val to [min, max]. */
export const clamp = (val, min, max) => Math.min(Math.max(val, min), max);

/** Normalize val to [0, 1] against a known ceiling. */
export const normalize = (val, max) => clamp(safeDiv(val, max), 0, 1);
