// TTL-based in-memory cache. Entries expire lazily on read — no timer sweep needed.

const store = new Map();

export function get(key) {
  const entry = store.get(key);
  if (entry === undefined) return undefined;
  if (Date.now() > entry.expiresAt) {
    store.delete(key);
    return undefined;
  }
  return entry.value;
}

export function set(key, value, ttlMs) {
  store.set(key, { value, expiresAt: Date.now() + ttlMs });
}

export function has(key) {
  return get(key) !== undefined;
}

export function del(key) {
  store.delete(key);
}

export function size() {
  return store.size;
}
