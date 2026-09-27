/**
 * template_cache.js — shared in-memory cache for flow templates.
 * Invalidate after any write operation so pickers stay fresh.
 */
import { api } from "./http.js";

let _cache = null;

export async function getTemplates() {
  if (!_cache) _cache = api.listFlowTemplates();
  return _cache;
}

export function invalidateTemplates() {
  _cache = null;
}
