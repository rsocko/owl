import { describe, expect, it } from 'vitest';
import { computeMatchRates, computeConfidence, formatShortDate, rateClass } from './History';

/* ================================================================
 * Unit tests for History page pure helper functions
 * ================================================================ */

describe('rateClass', () => {
  it('returns "excellent" for rates >= 90', () => {
    expect(rateClass(90)).toBe('excellent');
    expect(rateClass(100)).toBe('excellent');
    expect(rateClass(95)).toBe('excellent');
  });
  it('returns "good" for rates 75-89', () => {
    expect(rateClass(75)).toBe('good');
    expect(rateClass(89)).toBe('good');
  });
  it('returns "fair" for rates 50-74', () => {
    expect(rateClass(50)).toBe('fair');
    expect(rateClass(74)).toBe('fair');
  });
  it('returns "poor" for rates < 50', () => {
    expect(rateClass(0)).toBe('poor');
    expect(rateClass(49)).toBe('poor');
  });
});

describe('formatShortDate', () => {
  it('formats a valid ISO date to short form', () => {
    const result = formatShortDate('2026-03-15T10:00:00Z');
    expect(result).toMatch(/Mar/);
    expect(result).toMatch(/15/);
  });
  it('returns the raw string for an invalid date', () => {
    expect(formatShortDate('not-a-date')).toBe('not-a-date');
  });
});

describe('computeMatchRates', () => {
  it('returns empty array for empty input', () => {
    expect(computeMatchRates([])).toEqual([]);
  });
  it('skips runs with zero documents_scanned', () => {
    const runs = [
      { id: 1, documents_scanned: 0, matches_found: 0, started_at: '2026-01-01T00:00:00Z' },
      { id: 2, documents_scanned: null, matches_found: 5, started_at: '2026-01-02T00:00:00Z' },
    ];
    expect(computeMatchRates(runs)).toEqual([]);
  });
  it('computes correct match rates', () => {
    const runs = [
      { id: 1, documents_scanned: 100, matches_found: 85, started_at: '2026-03-01T00:00:00Z' },
      { id: 2, documents_scanned: 50, matches_found: 45, started_at: '2026-04-01T00:00:00Z' },
    ];
    const result = computeMatchRates(runs);
    expect(result).toHaveLength(2);
    expect(result[0].rate).toBe(85);
    expect(result[1].rate).toBe(90);
    expect(result[0].runId).toBe(1);
  });
  it('handles null matches_found as 0', () => {
    const runs = [{ id: 1, documents_scanned: 100, matches_found: null, started_at: '2026-01-01T00:00:00Z' }];
    const result = computeMatchRates(runs);
    expect(result[0].rate).toBe(0);
  });
  it('uses run id as label when started_at is missing', () => {
    const runs = [{ id: 42, documents_scanned: 10, matches_found: 5 }];
    const result = computeMatchRates(runs);
    expect(result[0].label).toBe('#42');
  });
});

describe('computeConfidence', () => {
  it('returns empty array for empty input', () => {
    expect(computeConfidence([])).toEqual([]);
  });
  it('skips runs with all-zero confidence', () => {
    const runs = [
      { id: 1, high_confidence: 0, medium_confidence: 0, low_confidence: 0, started_at: '2026-01-01T00:00:00Z' },
    ];
    expect(computeConfidence(runs)).toEqual([]);
  });
  it('computes correct confidence breakdown', () => {
    const runs = [
      { id: 1, high_confidence: 10, medium_confidence: 5, low_confidence: 2, started_at: '2026-03-01T00:00:00Z' },
    ];
    const result = computeConfidence(runs);
    expect(result).toHaveLength(1);
    expect(result[0].high).toBe(10);
    expect(result[0].medium).toBe(5);
    expect(result[0].low).toBe(2);
    expect(result[0].total).toBe(17);
  });
  it('handles null confidence values as 0', () => {
    const runs = [
      { id: 1, high_confidence: 5, medium_confidence: null, low_confidence: null, started_at: '2026-01-01T00:00:00Z' },
    ];
    const result = computeConfidence(runs);
    expect(result).toHaveLength(1);
    expect(result[0].high).toBe(5);
    expect(result[0].medium).toBe(0);
    expect(result[0].low).toBe(0);
    expect(result[0].total).toBe(5);
  });
});
