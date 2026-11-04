import type { Message, Session } from '../types'

const DB_NAME = 'MiaoyiDB'
const DB_VERSION = 2

interface StoredMessage extends Message {
  sessionId: string
  audioData?: ArrayBuffer
}

class DBManager {
  private db: IDBDatabase | null = null
  private dbPromise: Promise<IDBDatabase> | null = null

  private openDB(): Promise<IDBDatabase> {
    if (this.dbPromise) {
      return this.dbPromise
    }

    this.dbPromise = new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, DB_VERSION)

      request.onerror = () => {
        this.dbPromise = null
        reject(request.error)
      }

      request.onsuccess = () => {
        this.db = request.result
        resolve(this.db)
      }

      request.onupgradeneeded = (event) => {
        const db = (event.target as IDBOpenDBRequest).result

        if (!db.objectStoreNames.contains('messages')) {
          const messageStore = db.createObjectStore('messages', { keyPath: 'id' })
          messageStore.createIndex('sessionId', 'sessionId', { unique: false })
          messageStore.createIndex('timestamp', 'timestamp', { unique: false })
        }

        if (!db.objectStoreNames.contains('sessions')) {
          const sessionStore = db.createObjectStore('sessions', { keyPath: 'id' })
          sessionStore.createIndex('id', 'id', { unique: true })
        }
      }
    })

    return this.dbPromise
  }

  async addMessage(message: Message, sessionId: string): Promise<void> {
    const db = await this.openDB()
    return new Promise((resolve, reject) => {
      const transaction = db.transaction(['messages'], 'readwrite')
      const store = transaction.objectStore('messages')
      
      const storedMessage: StoredMessage = { ...message, sessionId }
      
      if (message.audioUrl && message.audioUrl.startsWith('blob:')) {
        fetch(message.audioUrl)
          .then(res => res.arrayBuffer())
          .then((arrayBuffer) => {
            storedMessage.audioData = arrayBuffer
            storedMessage.audioUrl = undefined
            const request = store.put(storedMessage)
            request.onsuccess = () => resolve()
            request.onerror = () => reject(request.error)
          })
          .catch(() => {
            const request = store.put(storedMessage)
            request.onsuccess = () => resolve()
            request.onerror = () => reject(request.error)
          })
      } else {
        const request = store.put(storedMessage)
        request.onsuccess = () => resolve()
        request.onerror = () => reject(request.error)
      }
    })
  }

  async addMessages(messages: Message[], sessionId: string): Promise<void> {
    const db = await this.openDB()
    return new Promise((resolve, reject) => {
      const transaction = db.transaction(['messages'], 'readwrite')
      const store = transaction.objectStore('messages')

      const promises = messages.map((message) => {
        return new Promise<void>((resolveMsg) => {
          const storedMessage: StoredMessage = { ...message, sessionId }
          
          if (message.audioUrl && message.audioUrl.startsWith('blob:')) {
            fetch(message.audioUrl)
              .then(res => res.arrayBuffer())
              .then((arrayBuffer) => {
                storedMessage.audioData = arrayBuffer
                storedMessage.audioUrl = undefined
                store.put(storedMessage)
                resolveMsg()
              })
              .catch(() => {
                store.put(storedMessage)
                resolveMsg()
              })
          } else {
            store.put(storedMessage)
            resolveMsg()
          }
        })
      })

      Promise.all(promises).then(() => {
        transaction.oncomplete = () => resolve()
      }).catch(() => {
        transaction.onerror = () => reject(transaction.error)
      })
    })
  }

  async getMessages(sessionId: string): Promise<Message[]> {
    const db = await this.openDB()
    return new Promise((resolve, reject) => {
      const transaction = db.transaction(['messages'], 'readonly')
      const store = transaction.objectStore('messages')
      const index = store.index('sessionId')
      const request = index.getAll(sessionId)

      request.onsuccess = () => {
        const storedMessages = request.result as StoredMessage[]
        const messages = storedMessages.map((m) => {
          const { sessionId: _, audioData, ...rest } = m
          const message = rest as Message
          
          if (audioData && m.msgType === 'voice') {
            const blob = new Blob([audioData], { type: 'audio/webm' })
            message.audioUrl = URL.createObjectURL(blob)
          }
          
          return message
        }).sort((a, b) => a.timestamp - b.timestamp)
        
        resolve(messages)
      }
      request.onerror = () => reject(request.error)
    })
  }

  async updateMessage(message: Message, sessionId: string): Promise<void> {
    const db = await this.openDB()
    return new Promise((resolve, reject) => {
      const transaction = db.transaction(['messages'], 'readwrite')
      const store = transaction.objectStore('messages')
      
      const storedMessage: StoredMessage = { ...message, sessionId }
      
      if (message.audioUrl && message.audioUrl.startsWith('blob:')) {
        fetch(message.audioUrl)
          .then(res => res.arrayBuffer())
          .then((arrayBuffer) => {
            storedMessage.audioData = arrayBuffer
            storedMessage.audioUrl = undefined
            const request = store.put(storedMessage)
            request.onsuccess = () => resolve()
            request.onerror = () => reject(request.error)
          })
          .catch(() => {
            const request = store.put(storedMessage)
            request.onsuccess = () => resolve()
            request.onerror = () => reject(request.error)
          })
      } else {
        const request = store.put(storedMessage)
        request.onsuccess = () => resolve()
        request.onerror = () => reject(request.error)
      }
    })
  }

  async deleteMessage(messageId: string): Promise<void> {
    const db = await this.openDB()
    return new Promise((resolve, reject) => {
      const transaction = db.transaction(['messages'], 'readwrite')
      const store = transaction.objectStore('messages')
      const request = store.delete(messageId)

      request.onsuccess = () => resolve()
      request.onerror = () => reject(request.error)
    })
  }

  async clearMessages(sessionId: string): Promise<void> {
    const db = await this.openDB()
    return new Promise((resolve, reject) => {
      const transaction = db.transaction(['messages'], 'readwrite')
      const store = transaction.objectStore('messages')
      const index = store.index('sessionId')
      const request = index.openCursor(sessionId)

      request.onsuccess = () => {
        const cursor = request.result
        if (cursor) {
          cursor.delete()
          cursor.continue()
        }
      }

      transaction.oncomplete = () => resolve()
      transaction.onerror = () => reject(transaction.error)
    })
  }

  async saveSession(session: Session): Promise<void> {
    const db = await this.openDB()
    return new Promise((resolve, reject) => {
      const transaction = db.transaction(['sessions'], 'readwrite')
      const store = transaction.objectStore('sessions')
      const request = store.put(session)

      request.onsuccess = () => resolve()
      request.onerror = () => reject(request.error)
    })
  }

  async getSessions(): Promise<Session[]> {
    const db = await this.openDB()
    return new Promise((resolve, reject) => {
      const transaction = db.transaction(['sessions'], 'readonly')
      const store = transaction.objectStore('sessions')
      const request = store.getAll()

      request.onsuccess = () => {
        resolve(request.result.sort((a, b) => (b.lastTime || 0) - (a.lastTime || 0)))
      }
      request.onerror = () => reject(request.error)
    })
  }

  async deleteSession(sessionId: string): Promise<void> {
    const db = await this.openDB()
    return new Promise((resolve, reject) => {
      const transaction = db.transaction(['sessions', 'messages'], 'readwrite')
      const sessionStore = transaction.objectStore('sessions')
      const messageStore = transaction.objectStore('messages')
      const messageIndex = messageStore.index('sessionId')

      sessionStore.delete(sessionId)

      const cursorRequest = messageIndex.openCursor(sessionId)
      cursorRequest.onsuccess = () => {
        const cursor = cursorRequest.result
        if (cursor) {
          cursor.delete()
          cursor.continue()
        }
      }

      transaction.oncomplete = () => resolve()
      transaction.onerror = () => reject(transaction.error)
    })
  }

  async clearAll(): Promise<void> {
    const db = await this.openDB()
    return new Promise((resolve, reject) => {
      const transaction = db.transaction(['sessions', 'messages'], 'readwrite')
      transaction.objectStore('sessions').clear()
      transaction.objectStore('messages').clear()

      transaction.oncomplete = () => resolve()
      transaction.onerror = () => reject(transaction.error)
    })
  }

  async getMessageCount(sessionId: string): Promise<number> {
    const db = await this.openDB()
    return new Promise((resolve, reject) => {
      const transaction = db.transaction(['messages'], 'readonly')
      const store = transaction.objectStore('messages')
      const index = store.index('sessionId')
      const request = index.count(sessionId)

      request.onsuccess = () => resolve(request.result)
      request.onerror = () => reject(request.error)
    })
  }
}

export const dbManager = new DBManager()