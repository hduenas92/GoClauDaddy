/**
 * Shared attachment rendering rules (F2).
 *
 * One definition of "what is an image attachment" and of how a stored file is
 * downloaded, so the composer chip and the history bubble can never disagree.
 *
 * The allowlist is the XSS line: ONLY png/jpg/jpeg/gif/webp ever become an
 * <img> whose src is the download endpoint. Everything else — including an
 * svg-named row — is rendered as a filename chip, never inlined. There is no
 * code path that inserts file content or a data: URL into the DOM.
 */

export const IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "gif", "webp"]);

export function isImageName(name) {
  const m = /\.([a-z0-9]+)$/i.exec(String(name ?? ""));
  return !!m && IMAGE_EXTENSIONS.has(m[1].toLowerCase());
}

export function attachmentDownloadUrl(id) {
  return `/api/attachments/${encodeURIComponent(id)}/download`;
}
