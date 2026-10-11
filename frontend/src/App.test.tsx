import { describe, expect, it } from 'vitest';
import { canonicalActionTarget } from './lib/canonicalAction';

describe('canonicalActionTarget', () => {
  it('routes actions needing review to their current review item', () => {
    expect(canonicalActionTarget({
      id: 306,
      status: 'pending',
      review_state: 'needs_review',
      needs_review_url: '#/needs-review?type=action_classification&item=1f3ad1a053e2',
    })).toBe('/needs-review?type=action_classification&item=1f3ad1a053e2');
  });

  it('routes ready actions to the Action Queue by durable identity', () => {
    expect(canonicalActionTarget({
      id: 306,
      status: 'pending',
      review_state: 'ready',
      needs_review_url: null,
    })).toBe('/action-queue?action=306&status=pending');
  });
});
