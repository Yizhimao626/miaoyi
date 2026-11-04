// -*- coding: utf-8 -*-
/**
 * 喵艺自定义 Hooks - 提供通用的 React 逻辑复用
 * 
 * 包含：
 * - useOnlineStatus: 网络状态检测
 * - useConnectionStatus: WebSocket 连接状态
 * - useHaptic: 触感反馈
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import { Capacitor } from '@capacitor/core';
import { Haptics, ImpactStyle, NotificationType } from '@capacitor/haptics';
import { getBaseUrl } from '../utils/api';

/**
 * 网络状态检测 Hook
 * 监听浏览器 online/offline 事件，返回当前网络状态
 * 当 WebSocket 已连接时，自动跳过冗余的 ping 检测（心跳已覆盖）
 * 
 * @param wsConnected - WebSocket 是否已连接（可选）
 * @returns {boolean} true 表示在线，false 表示离线
 * 
 * @example
 * // 基本用法
 * const isOnline = useOnlineStatus();
 * 
 * // 与 WebSocket 联动：连接时不做冗余检测
 * const { connectionStatus } = useWebSocket();
 * const isOnline = useOnlineStatus(connectionStatus === 'connected');
 */
export function useOnlineStatus(wsConnected?: boolean): boolean {
  const [isOnline, setIsOnline] = useState<boolean>(() => {
    if (typeof navigator !== 'undefined') {
      return navigator.onLine;
    }
    return true;
  });

  useEffect(() => {
    const handleOnline = () => setIsOnline(true);
    const handleOffline = () => setIsOnline(false);

    window.addEventListener('online', handleOnline);
    window.addEventListener('offline', handleOffline);

    // 主动检测网络状态 - 仅在 WebSocket 未连接时启用
    // 当 WebSocket 已连接时，心跳机制已覆盖连接检测，无需冗余 ping
    const checkConnection = async () => {
      // 如果 WebSocket 已连接，跳过 ping（心跳已在工作）
      if (wsConnected) {
        // 但仍更新在线状态（基于浏览器事件）
        return;
      }

      try {
        // 【修复】只要 fetch 能拿到 HTTP 响应（不论状态码）就说明网络通：
        // - 200：正常
        // - 405/404：后端在线但端点方法不支持/不存在（网络是通的）
        // - 500+：后端在线但服务异常（网络仍是通的）
        // 只有 fetch 抛异常（TypeError/AbortError/超时）才说明网络断
        await fetch(`${getBaseUrl()}/health`, {
          method: 'HEAD',
          cache: 'no-store',
          signal: AbortSignal.timeout(3000)
        })
        setIsOnline(true)
      } catch {
        // fetch 抛异常 = 网络层失败（DNS/TCP/超时/CORS 拒绝）
        setIsOnline(false)
      }
    };

    // 动态调整检测间隔：
    // - WebSocket 已连接时：60秒（低频检查，主要靠心跳）
    // - WebSocket 未连接时：15秒（更频繁检测以便快速恢复）
    const interval = wsConnected ? 60000 : 15000;
    const intervalId = setInterval(checkConnection, interval);

    // 立即执行一次检测
    checkConnection();

    return () => {
      window.removeEventListener('online', handleOnline);
      window.removeEventListener('offline', handleOffline);
      clearInterval(intervalId);
    };
  }, [wsConnected]); // 当 WebSocket 连接状态变化时重新设置

  return isOnline;
}

/**
 * 连接状态 Hook
 * 综合网络状态和 WebSocket 连接状态，返回完整的连接状态信息
 * 
 * @returns {Object} 连接状态对象
 * @property {boolean} isOnline - 网络是否在线
 * @property {boolean} isConnected - WebSocket 是否已连接
 * @property {'online'|'offline'|'connecting'|'disconnected'} status - 连接状态字符串
 * @property {string} statusText - 中文状态描述
 */
export function useConnectionStatus(connectionStatus: string = 'connected') {
  // 将 WebSocket 连接状态传递给 useOnlineStatus，实现智能检测
  const isOnline = useOnlineStatus(connectionStatus === 'connected');

  let status: 'online' | 'offline' | 'connecting' | 'disconnected';
  let statusText: string;

  if (!isOnline) {
    status = 'offline';
    statusText = '网络被猫咬断了...喵～';
  } else {
    switch (connectionStatus) {
      case 'connected':
        status = 'online';
        statusText = '🐱 在线';
        break;
      case 'connecting':
        status = 'connecting';
        statusText = '🐱 正在连接...';
        break;
      case 'disconnected':
        status = 'disconnected';
        statusText = '🐱 连接已断开，正在重连...';
        break;
      default:
        status = 'online';
        statusText = '🐱 在线';
    }
  }

  return {
    isOnline,
    isConnected: isOnline && connectionStatus === 'connected',
    status,
    statusText,
  };
}

/**
 * 触感反馈 Hook
 * 支持移动端震动反馈和桌面端微动画
 * 
 * @returns {Function} 触发震动的函数
 * 
 * @example
 * const haptic = useHaptic();
 * haptic('light'); // 轻微震动
 */
export function useHaptic() {
  const trigger = useCallback((style: 'light' | 'medium' | 'heavy' | 'success' | 'warning' | 'error' = 'light') => {
    // 【移动适配】Capacitor 原生环境（含 iOS）走 Haptics 插件：
    // navigator.vibrate 在 iOS WebView 中完全无效
    if (Capacitor.isNativePlatform()) {
      const isNotification = style === 'success' || style === 'warning' || style === 'error'
      if (isNotification) {
        const notifyType = {
          success: NotificationType.Success,
          warning: NotificationType.Warning,
          error: NotificationType.Error,
        }[style]
        Haptics.notification({ type: notifyType }).catch(() => { /* 老设备/设置静音时静默 */ })
      } else {
        const impactStyle = {
          light: ImpactStyle.Light,
          medium: ImpactStyle.Medium,
          heavy: ImpactStyle.Heavy,
        }[style]
        Haptics.impact({ style: impactStyle }).catch(() => {})
      }
    } else if (typeof navigator !== 'undefined' && 'vibrate' in navigator) {
      // Web / Android 浏览器环境：系统振动
      const patterns: Record<string, number | number[]> = {
        'light': 10,
        'medium': 20,
        'heavy': 30,
        'success': [10, 50, 20],
        'warning': [30, 30, 30],
        'error': [50, 50, 50],
      };
      navigator.vibrate(patterns[style]);
    }

    // 可选：通过自定义事件触发 CSS 动画
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('miaoyi:haptic', {
        detail: { style, timestamp: Date.now() }
      }));
    }
  }, []);

  return trigger;
}

/**
 * 节流 Hook
 * 限制函数调用频率
 * 
 * @param callback - 需要节流的函数
 * @param delay - 最小间隔时间（毫秒）
 * @returns 节流后的函数
 */
export function useThrottle<T extends (...args: any[]) => any>(callback: T, delay: number): T {
  const lastCall = useRef<number>(0);

  return useCallback(
    (...args: Parameters<T>): ReturnType<T> | undefined => {
      const now = Date.now();
      if (now - lastCall.current >= delay) {
        lastCall.current = now;
        return callback(...args) as ReturnType<T>;
      }
    },
    [callback, delay]
  ) as T;
}

/**
 * 防抖 Hook
 * 延迟函数调用，只有在停止操作后才执行
 * 
 * @param callback - 需要防抖的函数
 * @param delay - 等待时间（毫秒）
 * @returns 防抖后的函数
 */
export function useDebounce<T extends (...args: any[]) => any>(callback: T, delay: number): T {
  const timeoutRef = useRef<NodeJS.Timeout | null>(null);

  return useCallback(
    (...args: Parameters<T>): void => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
      timeoutRef.current = setTimeout(() => {
        callback(...args);
      }, delay);
    },
    [callback, delay]
  ) as unknown as T;
}
