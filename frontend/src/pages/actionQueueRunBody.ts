export function buildQueueRunBody(dryRun: boolean, documentId?: string | number | null) {
  const body: Record<string, unknown> = { dry_run: dryRun };
  const hasSpecificDocument =
    documentId !== null && documentId !== undefined && String(documentId).trim() !== '';
  if (!dryRun && hasSpecificDocument) {
    body.force = true;
  }
  return body;
}
