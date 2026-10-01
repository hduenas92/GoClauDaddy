/**
 * Safe wrapper around the browser's localStorage API.
 *
 * Browser privacy settings can make every access to window.localStorage throw a
 * SecurityError. These helpers swallow that, so callers can read and write UI
 * preferences without a blocked origin preventing the app from starting.
 */

function storage() {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/** The stored string, or null if the key is missing or storage is unavailable. */
export function getItem(key) {
  try {
    return storage()?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

/** Stores the value, or silently does nothing if storage is unavailable. */
export function setItem(key, value) {
  try {
    storage()?.setItem(key, value);
  } catch {
    /* storage unavailable — nothing to persist */
  }
}

/** Removes the key, or silently does nothing if storage is unavailable. */
export function removeItem(key) {
  try {
    storage()?.removeItem(key);
  } catch {
    /* storage unavailable — nothing to remove */
  }
}