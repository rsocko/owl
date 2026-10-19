import { describe, expect, it } from 'vitest';
import { canonicalActionTarget } from './lib/canonicalAction';
import { legacyOcrQueueTarget } from './lib/ocrNavigation';
import { legacySourceTarget } from './lib/routeTargets';

describe('canonicalActionTarget', () => {
  it('routes actions needing review to their current review item', () => {
    expect(canonicalActionTarget({
      id: 306,
      status: 'pending',
      review_state: 'needs_review',
      needs_review_url: '#/needs-review?type=action_classification&item=1f3ad1a053e2',
    })).toBe('/needs-review?type=action_classification&item=1f3ad1a053e2');
  });

  describe('legacyOcrQueueTarget', () => {
    it('redirects the standalone OCR queue into filtered Needs Review', () => {
      expect(legacyOcrQueueTarget('?review_status=FAILED&resolved=false&correspondent=9')).toBe(
        '/needs-review?type=ocr_quality_review&ocr_review_status=FAILED&status=pending&q=9',
      );
    });
  });

  it('routes ready actions to the Action Queue by durable identity', () => {
    expect(canonicalActionTarget({
      id: 306,
      status: 'pending',
      review_state: 'ready',
      needs_review_url: null,
    })).toBe('/actions?action=306&status=pending');
  });

  it('redirects legacy correspondent URLs to canonical Document Sources deep links', () => {
    expect(legacySourceTarget('41', '?mode=review')).toBe('/sources/41?mode=review');
    expect(legacySourceTarget(undefined, '?q=bank')).toBe('/sources?q=bank');
  });
});
