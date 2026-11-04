import { useState, useEffect, useRef } from 'react'

interface PerformanceMonitorProps {
  isVisible?: boolean
}

export function PerformanceMonitor({ isVisible = false }: PerformanceMonitorProps) {
  const [fps, setFps] = useState(60)
  const [startupTime, setStartupTime] = useState(0)
  const frameCountRef = useRef(0)
  const lastTimeRef = useRef(performance.now())
  const startTimeRef = useRef(performance.now())

  useEffect(() => {
    setStartupTime(Math.round(performance.now() - startTimeRef.current))
  }, [])

  useEffect(() => {
    if (!isVisible) return

    const measureFps = () => {
      frameCountRef.current++
      const now = performance.now()
      const elapsed = now - lastTimeRef.current

      if (elapsed >= 1000) {
        setFps(Math.round(frameCountRef.current * 1000 / elapsed))
        frameCountRef.current = 0
        lastTimeRef.current = now
      }

      requestAnimationFrame(measureFps)
    }

    const animationId = requestAnimationFrame(measureFps)
    return () => cancelAnimationFrame(animationId)
  }, [isVisible])

  if (!isVisible) return null

  return (
    <div className="performance-monitor">
      <div className="perf-item">
        <span className="perf-label">FPS</span>
        <span className={`perf-value ${fps < 30 ? 'bad' : fps < 50 ? 'warning' : 'good'}`}>{fps}</span>
      </div>
      <div className="perf-item">
        <span className="perf-label">启动</span>
        <span className="perf-value">{startupTime}ms</span>
      </div>
    </div>
  )
}
