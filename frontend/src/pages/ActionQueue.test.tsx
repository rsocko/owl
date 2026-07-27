import { describe, expect, it } from 'vitest';

import { buildQueueRunBody } from './actionQueueRunBody';

describe('buildQueueRunBody', () => {
  it('does not force a normal live rerun', () => {
    expect(buildQueueRunBody(false)).toEqual({ dry_run: false });
  });

  it('does not force a dry run', () => {
    expect(buildQueueRunBody(true, 1234)).toEqual({ dry_run: true });
  });

  it('forces an explicit single-document live rerun', () => {
    expect(buildQueueRunBody(false, 1234)).toEqual({ dry_run: false, force: true });
  });
});
