import { useCallback, useRef, useState } from 'react'

// A persisted left/right split inside one container: the left pane's share of
// the container width, clamped so neither side gets too narrow to use.
const MIN = 0.3
const MAX = 0.78

function clamp(f: number): number {
  return Math.min(MAX, Math.max(MIN, f))
}

function load(key: string, fallback: number): number {
  try {
    const n = parseFloat(localStorage.getItem(key) ?? '')
    if (Number.isFinite(n)) return clamp(n)
  } catch {
    // Storage may be unavailable; use the default.
  }
  return fallback
}

export function useSplit(key: string, fallback = 0.6) {
  const [fraction, setFractionState] = useState(() => load(key, fallback))
  const container = useRef<HTMLDivElement>(null)

  const setFraction = useCallback(
    (f: number) => {
      const c = clamp(f)
      setFractionState(c)
      try {
        localStorage.setItem(key, String(c))
      } catch {
        // Persistence is best-effort.
      }
    },
    [key],
  )

  const startResize = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault()
      const box = container.current?.getBoundingClientRect()
      if (!box) return
      const onMove = (ev: MouseEvent) => setFraction((ev.clientX - box.left) / box.width)
      const onUp = () => {
        document.body.style.userSelect = ''
        document.body.style.cursor = ''
        window.removeEventListener('mousemove', onMove)
        window.removeEventListener('mouseup', onUp)
      }
      document.body.style.userSelect = 'none'
      document.body.style.cursor = 'col-resize'
      window.addEventListener('mousemove', onMove)
      window.addEventListener('mouseup', onUp)
    },
    [setFraction],
  )

  const reset = useCallback(() => setFraction(fallback), [fallback, setFraction])

  return { fraction, container, startResize, setFraction, reset }
}
