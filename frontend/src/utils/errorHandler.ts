export interface ErrorResponse {
  message: string
  code?: number
  details?: any
}

export class AppError extends Error {
  code: number
  details?: any

  constructor(message: string, code: number = 500, details?: any) {
    super(message)
    this.name = 'AppError'
    this.code = code
    this.details = details
  }
}

export const handleApiError = (error: unknown): ErrorResponse => {
  if (error instanceof AppError) {
    return {
      message: error.message,
      code: error.code,
      details: error.details,
    }
  }

  if (error instanceof Error) {
    if (error.message.includes('网络连接失败') || error.message.includes('网络断开')) {
      const isAndroid = /Android/i.test(navigator.userAgent)
      if (isAndroid) {
        return { 
          message: '喵～连不上服务器呢，检查下：1.手机和电脑在同一WiFi 2.电脑防火墙允许访问 3.后端服务器在不在运行哦~', 
          code: 0,
          details: { type: 'network' }
        }
      }
      return { message: '喵～网络好像断了，本喵正在努力重连中...', code: 0 }
    }
    if (error.message.includes('请求失败')) {
      return { message: '喵～请求失败了，让本喵试试别的方式~', code: 500 }
    }
    if (error.message.includes('数据格式错误')) {
      return { message: '喵～数据好像跑丢了，让本喵重新找找~', code: 500 }
    }
    return { message: error.message, code: 500 }
  }

  return { message: '喵～发生了一点小意外，本喵正在处理~', code: 500 }
}

export const getErrorMessage = (error: unknown): string => {
  const response = handleApiError(error)
  return response.message
}

export const isAuthError = (error: unknown): boolean => {
  const response = handleApiError(error)
  return response.code === 401 || response.message.includes('登录')
}

export const retry = async <T>(
  fn: () => Promise<T>,
  maxRetries: number = 3,
  delay: number = 1000,
  maxDelay: number = 30000
): Promise<T> => {
  let lastError: unknown
  for (let i = 0; i < maxRetries; i++) {
    try {
      return await fn()
    } catch (error) {
      lastError = error
      if (i < maxRetries - 1) {
        const backoffDelay = Math.min(delay * Math.pow(2, i), maxDelay)
        await new Promise((resolve) => setTimeout(resolve, backoffDelay))
      }
    }
  }
  throw lastError
}