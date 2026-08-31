import { useCallback, useEffect, useState } from 'react'

// Shelf-width model for the Assistant push-pane (issue #58 / spec #56, stories
// 24–27). The shelf shares horizontal space with <main> as a resizable split
// pane; the chosen width persists across sessions and is clamped to
// [MIN, half-viewport]. When the shelf reaches the half-viewport threshold the
// left nav has no comfortable room left, so Layout collapses it — derived here
// as `collapsed`, never a manual toggle.
const KEY = 'assistant.shelf.width'
const MIN = 320
const DEFAULT = 420

// The shelf is clamped to at most half the viewport, which is also the point at
// which the sidebar can no longer sit comfortably beside it.
function half(): number {
  return Math.floor(window.innerWidth / 2)
}

function clamp(w: number): number {
  // half-viewport is the hard ceiling; on a viewport too narrow for MIN to fit
  // under it (< 640px) the ceiling wins, so the shelf never exceeds 50vw.
  const max = half()
  return Math.max(Math.min(MIN, max), Math.min(w, max))
}

function load(): number {
  try {
    const raw = localStorage.getItem(KEY)
    if (raw) {
      const n = parseInt(raw, 10)
      if (Number.isFinite(n)) return clamp(n)
    }
  } catch {
    // localStorage may be unavailable (private mode, blocked storage); fall back.
  }
  return clamp(DEFAULT)
}

export interface ShelfWidth {
  width: number
  // True once the shelf has grown to the half-viewport threshold, i.e. the
  // sidebar should collapse. Recomputed on drag and on window resize.
  collapsed: boolean
  // mousedown handler for the drag handle on the shelf's left edge.
  startResize: (e: React.MouseEvent) => void
}

export function useShelfWidth(): ShelfWidth {
  const [width, setWidthState] = useState<number>(load)
  const [vw, setVw] = useState<number>(() => window.innerWidth)

  const setWidth = useCallback((w: number) => {
    const clamped = clamp(w)
    setWidthState(clamped)
    try {
      localStorage.setItem(KEY, String(clamped))
    } catch {
      // Persistence is best-effort; the live width still updates.
    }
  }, [])

  // Keep the shelf within [MIN, half-viewport] as the window resizes, and track
  // the viewport width so `collapsed` re-derives when it changes on its own.
  useEffect(() => {
    const onResize = () => {
      setVw(window.innerWidth)
      setWidthState((w) => clamp(w))
    }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  const startResize = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault()
      // The shelf is pinned to the right edge, so its width is the distance from
      // the cursor to that edge.
      const onMove = (ev: MouseEvent) => setWidth(window.innerWidth - ev.clientX)
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
    [setWidth],
  )

  return { width, collapsed: width >= Math.floor(vw / 2), startResize }
}
