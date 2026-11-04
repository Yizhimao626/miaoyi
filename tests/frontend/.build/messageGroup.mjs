// src/utils/messageGroup.ts
var INTERNAL_MESSAGE_TYPES = ["thinking", "thought_bubble"];
var GROUP_GAP_MS = 2 * 60 * 1e3;
var TIME_GAP_MIN = 5;
function groupMessages(messages) {
  if (messages.length === 0) return [];
  const sortedMessages = [...messages].sort((a, b) => a.timestamp - b.timestamp);
  const grouped = [];
  let lastTimestamp = 0;
  let currentGroup = [];
  const tempGroups = [];
  for (let i = 0; i < sortedMessages.length; i++) {
    const message = sortedMessages[i];
    if (INTERNAL_MESSAGE_TYPES.includes(message.msgType || "")) {
      continue;
    }
    if (currentGroup.length === 0) {
      currentGroup.push(message);
    } else {
      const lastMessage = currentGroup[currentGroup.length - 1];
      const timeDiff = message.timestamp - lastMessage.timestamp;
      if (timeDiff > GROUP_GAP_MS) {
        tempGroups.push(currentGroup);
        currentGroup = [message];
      } else {
        currentGroup.push(message);
      }
    }
  }
  if (currentGroup.length > 0) {
    tempGroups.push(currentGroup);
  }
  tempGroups.forEach((group) => {
    if (group.length === 0) return;
    const groupStart = group[0].timestamp;
    const currentTime = Math.floor(groupStart / 6e4);
    if (currentTime - lastTimestamp > TIME_GAP_MIN || lastTimestamp === 0) {
      grouped.push({
        type: "time",
        timestamp: groupStart
      });
      lastTimestamp = currentTime;
    }
    group.forEach((message, index) => {
      grouped.push({
        type: "message",
        message,
        isGrouped: group.length > 1,
        groupIndex: index,
        groupSize: group.length
      });
    });
  });
  return grouped;
}
function formatTime(timestamp) {
  const date = new Date(timestamp);
  const hours = date.getHours().toString().padStart(2, "0");
  const minutes = date.getMinutes().toString().padStart(2, "0");
  return `${hours}:${minutes}`;
}
function formatDate(timestamp) {
  const date = new Date(timestamp);
  const now = /* @__PURE__ */ new Date();
  if (date.toDateString() === now.toDateString()) {
    return "\u4ECA\u5929";
  }
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  if (date.toDateString() === yesterday.toDateString()) {
    return "\u6628\u5929";
  }
  const diffDays = Math.floor((now.getTime() - date.getTime()) / (24 * 60 * 60 * 1e3));
  if (diffDays < 7) {
    const weekdays = ["\u5468\u65E5", "\u5468\u4E00", "\u5468\u4E8C", "\u5468\u4E09", "\u5468\u56DB", "\u5468\u4E94", "\u5468\u516D"];
    return weekdays[date.getDay()];
  }
  return `${date.getMonth() + 1}\u6708${date.getDate()}\u65E5`;
}
function searchMessages(messages, keyword) {
  if (!keyword.trim()) return [];
  const lowerKeyword = keyword.toLowerCase();
  return messages.filter((msg) => {
    if (msg.text && msg.text.toLowerCase().includes(lowerKeyword)) {
      return true;
    }
    if (msg.smallThought && msg.smallThought.toLowerCase().includes(lowerKeyword)) {
      return true;
    }
    if (msg.thought && msg.thought.toLowerCase().includes(lowerKeyword)) {
      return true;
    }
    return false;
  });
}
function getUnreadCount(messages, lastReadTimestamp) {
  return messages.filter((m) => m.timestamp > lastReadTimestamp && m.role === "ai").length;
}
function hasNewMessages(messages, lastReadTimestamp) {
  return messages.some((m) => m.timestamp > lastReadTimestamp && m.role === "ai");
}
export {
  formatDate,
  formatTime,
  getUnreadCount,
  groupMessages,
  hasNewMessages,
  searchMessages
};
