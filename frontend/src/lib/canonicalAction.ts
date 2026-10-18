export interface CanonicalActionLocation {
  id: number;
  status?: string | null;
  review_state?: string | null;
  needs_review_url?: string | null;
}

export function canonicalActionTarget(action: CanonicalActionLocation): string {
  if (action.review_state === 'needs_review' && action.needs_review_url) {
    return action.needs_review_url.replace(/^#/, '');
  }
  const params = new URLSearchParams({ action: String(action.id) });
  if (action.status) params.set('status', action.status);
  return `/actions?${params.toString()}`;
}
