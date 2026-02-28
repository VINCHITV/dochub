'use client'

import { useCallback, useRef } from 'react'

type SSEEvent = Record<string, unknown>

interface UseSSEStreamOptions {
  onEvent: (event: SSEEvent) => void
  onError?: (err: Error) => void
  onDone?: () => void
}

export function useSSEStream({ onEvent, onError, onDone }: UseSSEStreamOptions) {
  const abortRef = useRef<AbortController | null>(null)

  const stream = useCallback(
    async (url: string, body: unknown) => {
      abortRef.current?.abort()
      const ctrl = new AbortController()
      abortRef.current = ctrl

      try {
        const res = await fetch(url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
          signal: ctrl.signal,
        })

        if (!res.ok || !res.body) {
          throw new Error(`HTTP ${res.status}`)
        }

        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''

        while (true) {
          const { done, value } = await reader.read()
          if (done) break

          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n')
          buffer = lines.pop() ?? ''

          for (const line of lines) {
            if (!line.startsWith('data: ')) continue
            const raw = line.slice(6).trim()
            if (!raw) continue
            try {
              const parsed: SSEEvent = JSON.parse(raw)
              // Skip heartbeat events — sent to prevent Railway proxy idle timeout
              if (parsed.heartbeat === true) continue
              onEvent(parsed)
              if (parsed.done === true) {
                onDone?.()
                return
              }
            } catch {
              // malformed JSON — ignore
            }
          }
        }
        onDone?.()
      } catch (err: unknown) {
        if (err instanceof Error && err.name === 'AbortError') return
        onError?.(err instanceof Error ? err : new Error(String(err)))
      }
    },
    [onEvent, onError, onDone]
  )

  const abort = useCallback(() => abortRef.current?.abort(), [])

  return { stream, abort }
}
