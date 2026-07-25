import { useCallback, useRef, useState } from 'react';

export interface StreamingProgress {
  stage: string;
  message: string;
  current: number;
  total: number;
}

export interface StreamingActionState {
  /** Whether the action is currently running. */
  running: boolean;
  /** Latest progress event from the SSE stream (null when idle). */
  progress: StreamingProgress | null;
  /** Error message if the action failed. */
  error: string | null;
}

/**
 * Hook that connects to an SSE endpoint and tracks progress events.
 *
 * The backend emits JSON objects with:
 *   { stage, message, current, total }         — progress updates
 *   { stage: "complete", result: {...} }        — successful completion
 *   { stage: "error", message: "..." }          — failure
 *
 * Returns `[state, run, cancel]`.
 *   - `run(url)` starts the SSE stream.
 *   - `cancel()` aborts the in-flight request.
 */
export function useStreamingAction(): [
  StreamingActionState,
  (url: string, onComplete?: () => void) => void,
  () => void,
] {
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState<StreamingProgress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setRunning(false);
    setProgress(null);
  }, []);

  const run = useCallback(
    (url: string, onComplete?: () => void) => {
      // Abort any previous run
      abortRef.current?.abort();

      const controller = new AbortController();
      abortRef.current = controller;
      setRunning(true);
      setProgress(null);
      setError(null);

      // Use fetch + ReadableStream to consume SSE (avoids EventSource limitation of GET-only)
      fetch(url, { signal: controller.signal })
        .then(async (res) => {
          if (!res.ok) {
            throw new Error(`Request failed with status ${res.status}`);
          }
          const reader = res.body?.getReader();
          if (!reader) throw new Error('No response body');
          const decoder = new TextDecoder();
          let buffer = '';

          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });

            // Parse SSE events: lines starting with "data: "
            const lines = buffer.split('\n');
            // Keep the last (possibly incomplete) line in the buffer
            buffer = lines.pop() ?? '';

            for (const line of lines) {
              const trimmed = line.trim();
              if (!trimmed.startsWith('data:')) continue;
              const jsonStr = trimmed.slice(5).trim();
              if (!jsonStr) continue;

              try {
                const event = JSON.parse(jsonStr);

                if (event.stage === 'error') {
                  setError(event.message ?? 'Unknown error');
                  setRunning(false);
                  setProgress(null);
                  return;
                }

                if (event.stage === 'complete') {
                  setRunning(false);
                  setProgress(null);
                  onComplete?.();
                  return;
                }

                // Progress update
                setProgress({
                  stage: event.stage ?? '',
                  message: event.message ?? '',
                  current: event.current ?? 0,
                  total: event.total ?? 0,
                });
              } catch {
                // ignore malformed JSON lines
              }
            }
          }

          // Stream ended without complete/error — treat as complete
          setRunning(false);
          setProgress(null);
          onComplete?.();
        })
        .catch((err) => {
          if (err instanceof DOMException && err.name === 'AbortError') {
            // User cancelled — not an error
            return;
          }
          setError(err instanceof Error ? err.message : 'Stream connection failed');
          setRunning(false);
          setProgress(null);
        });
    },
    [],
  );

  return [{ running, progress, error }, run, cancel];
}
