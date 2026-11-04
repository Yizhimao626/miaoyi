import React from 'react'
import { Icon } from './icons'

interface ErrorBoundaryProps {
  children: React.ReactNode
  fallback?: React.ReactNode
  onError?: (error: Error, errorInfo: React.ErrorInfo) => void
}

interface ErrorBoundaryState {
  hasError: boolean
  error: Error | null
  errorInfo: React.ErrorInfo | null
}

export class ErrorBoundary extends React.Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props)
    this.state = {
      hasError: false,
      error: null,
      errorInfo: null,
    }
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return {
      hasError: true,
      error,
      errorInfo: null,
    }
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    this.setState({
      hasError: true,
      error,
      errorInfo,
    })

    // 记录错误日志
    console.error('[ErrorBoundary] 捕获到错误:', error)
    console.error('[ErrorBoundary] 错误信息:', errorInfo)

    // 调用自定义错误处理
    if (this.props.onError) {
      this.props.onError(error, errorInfo)
    }

    // 发送错误到监控 (预留接口)
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('miaoyi:error', {
        detail: {
          message: error.message,
          stack: error.stack,
          componentStack: errorInfo.componentStack,
          timestamp: Date.now(),
        }
      }))
    }
  }

  handleReset = () => {
    this.setState({
      hasError: false,
      error: null,
      errorInfo: null,
    })
  }

  handleReload = () => {
    window.location.reload()
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback
      }

      return (
        <div className="error-boundary-fallback">
          <div className="error-container">
            <div className="error-header">
              <span className="error-icon"><Icon name="cat" size={32} /></span>
              <h2 className="error-title">喵~ 出问题了</h2>
            </div>
            
            <div className="error-content">
              <p className="error-message">
                {this.state.error?.message || '组件渲染出现了异常'}
              </p>
              
              {this.state.errorInfo?.componentStack && (
                <details className="error-details">
                  <summary>错误详情</summary>
                  <pre>{this.state.errorInfo.componentStack}</pre>
                </details>
              )}
            </div>
            
            <div className="error-actions">
              <button 
                className="error-btn btn-primary"
                onClick={this.handleReset}
              >
                重试
              </button>
              <button 
                className="error-btn btn-secondary"
                onClick={this.handleReload}
              >
                刷新页面
              </button>
            </div>
            
            <div className="error-tip">
              <p>如果问题持续出现，请联系开发者或刷新页面</p>
            </div>
          </div>

          <style>{`
            .error-boundary-fallback {
              display: flex;
              align-items: center;
              justify-content: center;
              min-height: 100vh;
              padding: 20px;
              background: linear-gradient(135deg, #FFF5F5 0%, #FFE8E8 100%);
            }
            
            .error-container {
              max-width: 500px;
              width: 100%;
              padding: 32px;
              background: white;
              border-radius: 20px;
              box-shadow: 0 10px 40px rgba(0, 0, 0, 0.1);
              text-align: center;
            }
            
            .error-header {
              margin-bottom: 24px;
            }
            
            .error-icon {
              font-size: 64px;
              display: block;
              margin-bottom: 12px;
            }
            
            .error-title {
              font-size: 24px;
              color: #333;
              margin: 0;
            }
            
            .error-content {
              margin-bottom: 24px;
            }
            
            .error-message {
              font-size: 14px;
              color: #666;
              line-height: 1.6;
              margin: 0 0 16px 0;
              padding: 12px;
              background: #f8f8f8;
              border-radius: 8px;
              word-break: break-all;
            }
            
            .error-details {
              text-align: left;
              margin-top: 12px;
            }
            
            .error-details summary {
              cursor: pointer;
              color: #999;
              font-size: 12px;
              padding: 8px;
              background: #f0f0f0;
              border-radius: 6px;
            }
            
            .error-details pre {
              margin-top: 8px;
              padding: 12px;
              background: #2d2d2d;
              color: #f8f8f8;
              border-radius: 8px;
              font-size: 12px;
              max-height: 200px;
              overflow-auto;
            }
            
            .error-actions {
              display: flex;
              gap: 12px;
              justify-content: center;
              margin-bottom: 16px;
            }
            
            .error-btn {
              padding: 12px 24px;
              border: none;
              border-radius: 12px;
              font-size: 14px;
              font-weight: 500;
              cursor: pointer;
              transition: all 0.2s ease;
            }
            
            .error-btn:hover {
              transform: translateY(-2px);
            }
            
            .error-btn:active {
              transform: translateY(0);
            }
            
            .btn-primary {
              background: linear-gradient(135deg, #FF9F7F, #FF6B9D);
              color: white;
            }
            
            .btn-secondary {
              background: #f0f0f0;
              color: #666;
            }
            
            .error-tip {
              font-size: 12px;
              color: #999;
              line-height: 1.5;
            }
            
            .error-tip p {
              margin: 0;
            }
          `}</style>
        </div>
      )
    }

    return this.props.children
  }
}

/** 全局错误边界 - 包装整个应用 */
export function GlobalErrorBoundary({ children }: { children: React.ReactNode }) {
  const handleError = (error: Error, _errorInfo: React.ErrorInfo) => {
    // 全局错误处理逻辑
    console.error('[GlobalErrorBoundary] 全局错误:', error)

    // 可以在这里添加错误上报逻辑
    // reportError(error, _errorInfo)
  }

  return (
    <ErrorBoundary onError={handleError}>
      {children}
    </ErrorBoundary>
  )
}

/** 组件级错误边界 - 用于包裹特定组件 */
export function ComponentErrorBoundary({ 
  children, 
  componentName = 'unknown' 
}: { 
  children: React.ReactNode
  componentName?: string
}) {
  const handleError = (error: Error, _errorInfo: React.ErrorInfo) => {
    console.error(`[ComponentErrorBoundary] ${componentName} 错误:`, error)
  }

  const fallback = (
    <div className="component-error">
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
        <Icon name="cat" size={14} /> {componentName} 加载失败
      </span>
      <style>{`
        .component-error {
          padding: 12px;
          background: #FFF5F5;
          border-radius: 8px;
          color: #666;
          font-size: 12px;
          text-align: center;
        }
      `}</style>
    </div>
  )

  return (
    <ErrorBoundary fallback={fallback} onError={handleError}>
      {children}
    </ErrorBoundary>
  )
}

/** API 错误处理工具函数 */
export function handleApiError(error: unknown): { message: string; code: number } {
  if (error instanceof Error) {
    const errorMessage = error.message || '未知错误'
    
    // 网络错误
    if (errorMessage.includes('Network Error') || errorMessage.includes('Failed to fetch')) {
      return { message: '网络连接失败，请检查网络', code: 0 }
    }
    
    // 超时
    if (errorMessage.includes('timeout') || errorMessage.includes('Timeout')) {
      return { message: '请求超时，请稍后重试', code: 408 }
    }
    
    return { message: errorMessage, code: 500 }
  }
  
  return { message: '未知错误', code: 500 }
}
