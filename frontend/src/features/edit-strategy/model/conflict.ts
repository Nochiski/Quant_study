/**
 * Revision conflict facts (WORKFLOW P3-07). The backend's 409 message names the server's
 * actual revision (`... expected=N actual=M`); when it does not, the revision history is the
 * authority. The user's text is never touched by a conflict.
 */
export const latestRevisionFromDetail = (detail: string): number | null => {
  const match = /(?:actual|latest_revision)=(\d+)/.exec(detail);
  return match ? Number(match[1]) : null;
};
