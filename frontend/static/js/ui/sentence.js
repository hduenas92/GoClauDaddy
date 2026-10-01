/** Ends `s` with a period unless it already ends in . ! or ? (trailing spaces trimmed); "" stays "". */
export function endSentence(s) {
  const t = String(s ?? "").trimEnd();
  return !t || /[.!?]$/.test(t) ? t : `${t}.`;
}
