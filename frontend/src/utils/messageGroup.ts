import type { Message } from '../types'

export interface GroupedMessage {
  type: 'time' | 'message'
  timestamp?: number
  message?: Message
  isGrouped?: boolean  // 是否与前一条消息属于同一组
  groupIndex?: number  // 在当前组中的索引
  groupSize?: number   // 当前组的大小
}

const INTERNAL_MESSAGE_TYPES = ['thinking', 'thought_bubble']
const GROUP_GAP_MS = 2 * 60 * 1000  // 2分钟间隔认为是不同的组
const TIME_GAP_MIN = 5  // 5分钟间隔显示时间戳分隔

export function groupMessages(messages: Message[]): GroupedMessage[] {
  if (messages.length === 0) return []

  // 先按时间排序
  const sortedMessages = [...messages].sort((a, b) => a.timestamp - b.timestamp)
  
  const grouped: GroupedMessage[] = []
  let lastTimestamp = 0
  let currentGroup: Message[] = []
  
  // 第一遍：按时间分组
  const tempGroups: Message[][] = []
  for (let i = 0; i < sortedMessages.length; i++) {
    const message = sortedMessages[i]
    
    // 跳过内部消息类型
    if (INTERNAL_MESSAGE_TYPES.includes(message.msgType || '')) {
      continue
    }
    
    // 检查是否属于当前组
    if (currentGroup.length === 0) {
      currentGroup.push(message)
    } else {
      const lastMessage = currentGroup[currentGroup.length - 1]
      const timeDiff = message.timestamp - lastMessage.timestamp
      
      // 如果时间差超过 GROUP_GAP_MS，开始新组
      if (timeDiff > GROUP_GAP_MS) {
        tempGroups.push(currentGroup)
        currentGroup = [message]
      } else {
        currentGroup.push(message)
      }
    }
  }
  
  // 添加最后一组
  if (currentGroup.length > 0) {
    tempGroups.push(currentGroup)
  }
  
  // 第二遍：生成带时间戳的分组消息
  tempGroups.forEach((group) => {
    if (group.length === 0) return
    
    const groupStart = group[0].timestamp
    const currentTime = Math.floor(groupStart / 60000)
    
    // 如果与上一个时间戳间隔超过 TIME_GAP_MIN 分钟，添加时间分隔
    if (currentTime - lastTimestamp > TIME_GAP_MIN || lastTimestamp === 0) {
      grouped.push({
        type: 'time',
        timestamp: groupStart,
      })
      lastTimestamp = currentTime
    }
    
    // 添加组内消息
    group.forEach((message, index) => {
      grouped.push({
        type: 'message',
        message,
        isGrouped: group.length > 1,
        groupIndex: index,
        groupSize: group.length,
      })
    })
  })

  return grouped
}

export function formatTime(timestamp: number): string {
  const date = new Date(timestamp)
  const hours = date.getHours().toString().padStart(2, '0')
  const minutes = date.getMinutes().toString().padStart(2, '0')
  return `${hours}:${minutes}`
}

export function formatDate(timestamp: number): string {
  const date = new Date(timestamp)
  const now = new Date()
  
  if (date.toDateString() === now.toDateString()) {
    return '今天'
  }
  
  const yesterday = new Date(now)
  yesterday.setDate(yesterday.getDate() - 1)
  if (date.toDateString() === yesterday.toDateString()) {
    return '昨天'
  }
  
  // 本周内
  const diffDays = Math.floor((now.getTime() - date.getTime()) / (24 * 60 * 60 * 1000))
  if (diffDays < 7) {
    const weekdays = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
    return weekdays[date.getDay()]
  }
  
  return `${date.getMonth() + 1}月${date.getDate()}日`
}

/**
 * 搜索消息 - 在消息列表中搜索关键词
 */
export function searchMessages(messages: Message[], keyword: string): Message[] {
  if (!keyword.trim()) return []
  
  const lowerKeyword = keyword.toLowerCase()
  return messages.filter(msg => {
    // 搜索消息文本
    if (msg.text && msg.text.toLowerCase().includes(lowerKeyword)) {
      return true
    }
    // 搜索小型想法
    if (msg.smallThought && msg.smallThought.toLowerCase().includes(lowerKeyword)) {
      return true
    }
    // 搜索想法
    if (msg.thought && msg.thought.toLowerCase().includes(lowerKeyword)) {
      return true
    }
    return false
  })
}

/**
 * 获取未读消息数
 */
export function getUnreadCount(messages: Message[], lastReadTimestamp: number): number {
  return messages.filter(m => m.timestamp > lastReadTimestamp && m.role === 'ai').length
}

/**
 * 检查是否有新消息
 */
export function hasNewMessages(messages: Message[], lastReadTimestamp: number): boolean {
  return messages.some(m => m.timestamp > lastReadTimestamp && m.role === 'ai')
}