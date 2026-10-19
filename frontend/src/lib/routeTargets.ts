export function legacySourceTarget(correspondentId?: string, search = ''): string {
  return `/sources${correspondentId ? `/${correspondentId}` : ''}${search}`;
}
