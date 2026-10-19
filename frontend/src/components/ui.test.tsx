import { act, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Toast } from './ui';

describe('Toast', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('dismisses success notifications after the standard duration by default', () => {
    vi.useFakeTimers();
    const onDismiss = vi.fn();

    render(<Toast message="Saved." onDismiss={onDismiss} />);

    act(() => {
      vi.advanceTimersByTime(2_999);
    });
    expect(onDismiss).not.toHaveBeenCalled();

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(onDismiss).toHaveBeenCalledOnce();
  });
});
