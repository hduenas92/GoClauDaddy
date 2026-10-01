// Context used by a turn = fresh input + cache reads + cache writes. On a resumed chat the cache reads are
// most of it, so input_tokens alone understated CTX% by multiples (QA unknown #1, 2026-10-01).
// Accepts a live usage event (cache_*_input_tokens) or a stored message (cache_*_tokens).
export function contextTokens(u) {
  if (!u) return 0;
  return (u.input_tokens || 0)
    + (u.cache_read_input_tokens || u.cache_read_tokens || 0)
    + (u.cache_creation_input_tokens || u.cache_creation_tokens || 0);
}
