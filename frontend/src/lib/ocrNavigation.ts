export function legacyOcrQueueTarget(search: string) {
  const legacy = new URLSearchParams(search);
  const next = new URLSearchParams();
  next.set('type', 'ocr_quality_review');

  const reviewStatus = legacy.get('review_status');
  if (['UNCERTAIN', 'REVIEW_RECOMMENDED', 'FAILED'].includes(reviewStatus ?? '')) {
    next.set('ocr_review_status', reviewStatus!);
  }

  const resolved = legacy.get('resolved');
  if (resolved === 'true') next.set('status', 'resolved');
  if (resolved === 'false') next.set('status', 'pending');

  const searchTerms = ['document_type', 'correspondent', 'downstream_outcome']
    .map((key) => legacy.get(key))
    .filter((value): value is string => Boolean(value));
  if (searchTerms.length) next.set('q', searchTerms.join(' '));

  return `/needs-review?${next.toString()}`;
}
