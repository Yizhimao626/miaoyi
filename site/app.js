const API_BASE = window.location.origin + '/api';
const WS_BASE = window.location.origin.replace('http', 'ws') + '/ws';

let websocket = null;
let isConnected = false;
let isRecording = false;
let mediaRecorder = null;
let audioChunks = [];
let currentUser = null;
let selectedEmotion = 'happy';

let heartbeatInterval = null;
let reconnectionDelay = 1000;
let reconnectionAttempts = 0;
const MAX_RECONNECTION_ATTEMPTS = 10;

const EMOTION_STAGES = ['陌生人', '朋友', '好友', '密友'];
const ACHIEVEMENTS = [
    { id: 'first_chat', icon: '💬', name: '初次对话' },
    { id: 'daily_chat', icon: '📅', name: '每日聊天' },
    { id: 'story_time', icon: '📖', name: '故事达人' },
    { id: 'music_lover', icon: '🎵', name: '音乐爱好者' },
    { id: 'memory_master', icon: '🧠', name: '记忆大师' },
    { id: 'emotion_pro', icon: '❤️', name: '情感专家' },
    { id: 'sticker_fun', icon: '😎', name: '表情达人' },
    { id: 'voice_star', icon: '🎤', name: '语音之星' },
];

function init() {
    const token = localStorage.getItem('token');
    if (token) {
        try {
            const payload = JSON.parse(atob(token.split('.')[1]));
            if (payload.exp * 1000 > Date.now()) {
                currentUser = {
                    id: payload.user_id,
                    username: payload.username,
                    role: payload.role || 'user'
                };
                showApp();
                connectWebSocket();
                loadGrowthData();
                updateAdminUI();
                initScrollToBottom();
                return;
            }
        } catch (e) {
            localStorage.removeItem('token');
        }
    }

    document.getElementById('loginPage').style.display = 'flex';
    document.getElementById('app').style.display = 'none';
}

function showApp() {
    document.getElementById('loginPage').style.display = 'none';
    document.getElementById('app').style.display = 'flex';
    unlockAudioContext();
    initQuickStickers();
    initEmojiStickerPanel();
    loadVoiceMode();
}

async function loadVoiceMode() {
    try {
        const response = await apiFetch(`${API_BASE}/voice-mode`);
        const data = await response.json();
        if (data.code === 200 && data.data) {
            document.getElementById('voiceModeName').textContent = data.data.voice_mode_name;
        }
    } catch (e) {
        console.warn('加载语音模式失败:', e);
    }
}

function toggleVoiceMenu() {
    const menu = document.getElementById('voiceMenu');
    const voiceBtn = document.querySelector('.header-btn[title="语音"]');
    
    console.log('切换语音');
    
    if (voiceBtn) {
        voiceBtn.style.filter = 'brightness(0.7)';
        setTimeout(() => {
            voiceBtn.style.filter = '';
        }, 200);
    }
    
    menu.classList.toggle('show');
}

async function setVoiceMode(mode) {
    try {
        const response = await apiFetch(`${API_BASE}/voice-mode`, {
            method: 'POST',
            body: { mode }
        });
        const data = await response.json();
        if (data.code === 200 && data.data) {
            document.getElementById('voiceModeName').textContent = data.data.voice_mode_name;
            showToast(data.message);
        }
    } catch (e) {
        console.error('切换语音模式失败:', e);
        showToast('切换语音模式失败');
    }
    document.getElementById('voiceMenu').classList.remove('show');
}

document.addEventListener('click', (e) => {
    const menu = document.getElementById('voiceMenu');
    const selector = document.querySelector('.voice-selector');
    if (menu.classList.contains('show') && !selector.contains(e.target)) {
        menu.classList.remove('show');
    }
});

function unlockAudioContext() {
    try {
        const AudioContext = window.AudioContext || window.webkitAudioContext;
        if (AudioContext) {
            const audioContext = new AudioContext();
            if (audioContext.state === 'suspended') {
                audioContext.resume();
            }
        }
    } catch (e) {
        console.warn('Audio context unlock failed:', e);
    }
}

document.addEventListener('click', unlockAudioContext, { once: true });
document.addEventListener('touchstart', unlockAudioContext, { once: true });

async function apiFetch(url, options = {}) {
    const token = localStorage.getItem('token');

    options.headers = options.headers || {};
    options.headers['Authorization'] = `Bearer ${token}`;

    if (options.body && typeof options.body !== 'string') {
        options.body = JSON.stringify(options.body);
        options.headers['Content-Type'] = 'application/json';
    }

    const response = await fetch(url, options);

    if (response.status === 401 || response.status === 403) {
        localStorage.removeItem('token');
        currentUser = null;
        showToast('登录已过期，请重新登录');
        document.getElementById('loginPage').style.display = 'flex';
        document.getElementById('app').style.display = 'none';
        throw new Error('Unauthorized');
    }

    return response;
}

function updateAdminUI() {
    const headerRight = document.querySelector('.header-right');
    let adminBtn = document.getElementById('adminBtn');

    if (currentUser && currentUser.role === 'admin') {
        if (!adminBtn) {
            adminBtn = document.createElement('button');
            adminBtn.id = 'adminBtn';
            adminBtn.className = 'settings-btn';
            adminBtn.innerHTML = '🛡️';
            adminBtn.title = '管理后台';
            adminBtn.onclick = openAdminPanel;
            headerRight.insertBefore(adminBtn, headerRight.firstChild);
        }
    } else {
        if (adminBtn) {
            adminBtn.remove();
        }
    }
}

function openAdminPanel() {
    showToast('管理后台功能开发中');
}

function switchTab(tab) {
    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.form-panel').forEach(panel => panel.classList.remove('active'));

    document.querySelector(`.tab-btn[onclick="switchTab('${tab}')"]`).classList.add('active');
    document.getElementById(`${tab}Form`).classList.add('active');
}

async function handleLogin() {
    const username = document.getElementById('loginUsername').value.trim();
    const password = document.getElementById('loginPassword').value;

    if (!username || !password) {
        showLoginError('请输入用户名和密码');
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/auth/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password })
        });

        const data = await response.json();

        if (data.code === 200) {
            localStorage.setItem('token', data.data.token);
            currentUser = {
                id: data.data.user_id,
                username: data.data.username,
                role: data.data.role || 'user'
            };
            showApp();
            connectWebSocket();
            loadGrowthData();
            updateAdminUI();
            showToast('登录成功！');
        } else {
            showLoginError(data.message || '登录失败');
        }
    } catch (error) {
        showLoginError('网络连接失败');
    }
}

function showLoginError(message) {
    document.getElementById('loginError').textContent = message;
}

function showRegisterError(message) {
    document.getElementById('registerError').textContent = message;
}

async function handleRegister() {
    const username = document.getElementById('registerUsername').value.trim();
    const password = document.getElementById('registerPassword').value;
    const confirmPassword = document.getElementById('registerConfirmPassword').value;

    if (!username || !password || !confirmPassword) {
        showRegisterError('请填写所有字段');
        return;
    }

    if (password.length < 6) {
        showRegisterError('密码至少需要6位');
        return;
    }

    if (password !== confirmPassword) {
        showRegisterError('两次密码输入不一致');
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/auth/register`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password })
        });

        const data = await response.json();

        if (data.code === 200) {
            showToast('注册成功！请登录');
            switchTab('login');
            document.getElementById('loginUsername').value = username;
        } else {
            showRegisterError(data.message || '注册失败');
        }
    } catch (error) {
        showRegisterError('网络连接失败');
    }
}

function connectWebSocket() {
    const token = localStorage.getItem('token');
    if (!token) return;

    if (heartbeatInterval) {
        clearInterval(heartbeatInterval);
        heartbeatInterval = null;
    }

    try {
        websocket = new WebSocket(`${WS_BASE}?token=${encodeURIComponent(token)}`);

        websocket.onopen = () => {
            isConnected = true;
            reconnectionDelay = 1000;
            reconnectionAttempts = 0;
            pingFailCount = 0;
            updateConnectionStatus(true);
            hideWsAlert();
            startHeartbeat();
            console.log('WebSocket连接成功');
        };

        websocket.onmessage = (event) => {
            const data = JSON.parse(event.data);
            handleWebSocketMessage(data);
        };

        websocket.onerror = (error) => {
            console.error('WebSocket错误:', error);
            isConnected = false;
            updateConnectionStatus(false);
            showWsAlert();
        };

        websocket.onclose = () => {
            isConnected = false;
            updateConnectionStatus(false);
            showWsAlert();
            scheduleReconnection();
        };

    } catch (error) {
        console.error('连接WebSocket失败:', error);
        showWsAlert();
        scheduleReconnection();
    }
}

let pingTimeout = null;
let pingFailCount = 0;

function startHeartbeat() {
    if (heartbeatInterval) {
        clearInterval(heartbeatInterval);
    }
    heartbeatInterval = setInterval(() => {
        if (websocket && websocket.readyState === WebSocket.OPEN) {
            websocket.send(JSON.stringify({ type: 'ping' }));
            pingFailCount++;
            pingTimeout = setTimeout(() => {
                if (pingFailCount >= 3) {
                    console.warn('心跳超时，断开连接');
                    websocket.close();
                }
            }, 10000);
        }
    }, 30000);
}

function scheduleReconnection() {
    if (reconnectionAttempts >= MAX_RECONNECTION_ATTEMPTS) {
        updateConnectionStatusText('😿 喵星信号丢失');
        return;
    }

    updateConnectionStatusText('🐱 连接中...');
    
    setTimeout(() => {
        connectWebSocket();
        reconnectionDelay = Math.min(reconnectionDelay * 2, 30000);
        reconnectionAttempts++;
    }, reconnectionDelay);
}

function updateConnectionStatus(connected) {
    if (connected) {
        updateConnectionStatusText('🐱 在线');
    } else {
        updateConnectionStatusText('🔴 离线');
    }
}

function updateConnectionStatusText(text) {
    const statusEl = document.getElementById('connectionStatusText');
    if (statusEl) {
        statusEl.textContent = text;
    }
}

function showWsAlert() {
    const alert = document.getElementById('wsAlert');
    if (alert) {
        alert.classList.add('visible');
    }
}

function hideWsAlert() {
    const alert = document.getElementById('wsAlert');
    if (alert) {
        alert.classList.remove('visible');
    }
}

function initScrollToBottom() {
    const container = document.getElementById('messagesContainer');
    const btn = document.getElementById('scrollToBottomBtn');
    if (!container || !btn) return;

    container.addEventListener('scroll', () => {
        const scrollDiff = container.scrollHeight - container.scrollTop - container.clientHeight;
        if (scrollDiff > 200) {
            btn.classList.add('visible');
        } else {
            btn.classList.remove('visible');
        }
    });

    btn.addEventListener('click', () => {
        container.scrollTop = container.scrollHeight;
        btn.classList.remove('visible');
    });
}

function handleWebSocketMessage(data) {
    switch (data.type) {
        case 'message':
            addMessage('ai', data.content, data.time, data.memory_tip, null, null, null, null, data.voice_text);
            hideTypingIndicator();
            break;

        case 'response':
            addMessage('ai', data.text, data.timestamp, data.memory_tip, data.sources, data.sticker, data.voice_tip, data.image_url, data.voice_text);
            hideTypingIndicator();
            if (data.thought) {
                addThinkingStatus(data.thought);
            }
            if (data.emotional_state) {
                updateEmotion({
                    emoji: data.emotional_state.emoji || '😊',
                    description: data.emotional_state.description || '',
                    intimacy: data.emotional_state.intimacy || 0,
                    dominant_emotion: data.emotional_state.dominant_emotion || 'neutral',
                    thought: data.small_thought || ''
                });
                updateEmotionBars(data.emotional_state);
            }
            break;

        case 'greeting':
            addMessage('ai', data.text, data.timestamp, null, null, null, null, null, data.voice_text);
            break;

        case 'system':
            addSystemMessage(data.message);
            break;

        case 'emotional_state':
            updateEmotionBars(data);
            updateEmotion({
                emoji: data.emoji || '😊',
                description: data.description || '',
                intimacy: data.intimacy || 0,
                dominant_emotion: data.dominant_emotion || 'calm'
            });
            break;

        case 'proactive':
            addProactiveMessage(data.content);
            break;

        case 'thought_bubble':
            addThoughtBubble(data.content, data.timestamp);
            break;

        case 'thinking':
            if (data.status === 'started') {
                addThinkingStatus('喵艺正在思考...');
            }
            break;

        case 'thinking_status':
            updateStatusIndicator(data.status, data.content);
            break;

        case 'thinking_step':
            const stepData = data.step;
            if (stepData) {
                addThinkingStep(stepData.step || 1, stepData.thought || '', stepData.total_steps || 0);
            }
            break;

        case 'emotion':
            updateEmotion(data.emotion);
            break;

        case 'typing':
            if (data.status === 'typing') {
                showTypingIndicator();
            } else if (data.status === 'done') {
                hideTypingIndicator();
            }
            break;

        case 'voice_result':
            addVoiceResult(data.text);
            break;

        case 'growth_update':
            updateGrowthData(data.data);
            break;

        case 'speech':
            if (data.text) {
                document.getElementById('messageInput').value = data.text;
            }
            break;

        case 'tts_result':
            if (data.audio_url) {
                playAudio(data.audio_url);
            }
            break;

        case 'speech_status':
            if (data.status === 'started') {
                addSystemMessage('🎤 喵艺开始说话了...');
            } else if (data.status === 'finished') {
                addSystemMessage('🎤 喵艺说完了～');
            } else if (data.status === 'error') {
                addSystemMessage('🔊 语音播放失败');
            }
            break;

        case 'pong':
            clearTimeout(pingTimeout);
            pingFailCount = 0;
            break;

        default:
            console.log('Unknown message type:', data.type);
    }
}

function addMessage(role, content, time, memoryTip, sources, sticker, voiceTip, imageUrl, voiceText, status = 'done') {
    const container = document.getElementById('messagesContainer');
    const messageElement = document.createElement('div');
    messageElement.className = `message ${role} message-slide-in`;
    messageElement.dataset.id = `msg-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;

    const avatar = role === 'user' ? '👤' : '🐱';
    const username = role === 'user' ? (currentUser ? currentUser.username : '我') : '喵艺';
    const timeStr = time ? formatTime(time) : formatTime(Date.now());

    let memoryTipHtml = '';
    if (memoryTip) {
        memoryTipHtml = `<div class="message-memory-tip">📝 我记下了：${memoryTip}</div>`;
    }

    let sourcesHtml = '';
    if (sources && sources.length > 0) {
        sourcesHtml = `<div class="message-sources">🔍 参考来源：${sources.slice(0, 3).map(s => `<a href="${s}" target="_blank">${s}</a>`).join(' | ')}</div>`;
    }

    let stickerHtml = '';
    if (sticker && role === 'ai') {
        stickerHtml = `<div class="message-sticker">${sticker}</div>`;
    }

    let voiceTipHtml = '';
    if (voiceTip && role === 'ai') {
        voiceTipHtml = `<div class="message-voice-tip">🔊 ${voiceTip}</div>`;
    }

    let imageHtml = '';
    if (imageUrl) {
        imageHtml = `<img src="${imageUrl}" class="message-image" alt="图片表情">`;
    }

    let playBtnHtml = '';
    if (role === 'ai' && voiceText) {
        playBtnHtml = `
            <button class="play-voice-btn" onclick="toggleMessageAudio(this)" title="点击播放语音">
                <span class="play-icon">🔊</span>
                <div class="voice-wave">
                    <span></span><span></span><span></span><span></span><span></span>
                </div>
            </button>
        `;
    }

    const displayContent = role === 'ai' ? formatMessageContent(content) : escapeHtml(content);

    let statusHtml = '';
    if (role === 'user') {
        const msgId = messageElement.dataset.id;
        const statusIcons = {
            sending: '<span>⏳</span>',
            failed: `<span class="message-status-failed" onclick="resendMessage('${msgId}')">❗</span>`,
            success: '<span>✓✓</span>'
        };
        statusHtml = `<div class="message-status ${status}" id="msg-status-${messageElement.dataset.id}">${statusIcons[status] || ''}</div>`;
    }

    messageElement.innerHTML = `
        <div class="message-avatar">${avatar}</div>
        <div class="message-content">
            <div class="message-username">${escapeHtml(username)}</div>
            <div class="message-bubble" oncontextmenu="showLongpressMenu(event, this)">
                ${displayContent}${imageHtml}
                ${playBtnHtml}
            </div>
            ${stickerHtml}
            ${memoryTipHtml}
            ${sourcesHtml}
            ${voiceTipHtml}
            <div class="message-time">${timeStr}</div>
            ${statusHtml}
        </div>
    `;

    if (role === 'ai' && voiceText) {
        messageElement.dataset.voiceText = voiceText;
    }

    container.insertBefore(messageElement, document.getElementById('typingIndicator'));
    
    if (role === 'user' && status === 'sending') {
        setupLongpressListener(messageElement);
    }

    scrollToBottom();

    if (role === 'ai' && voiceText && !isMuted()) {
        setTimeout(() => {
            autoPlayAudio(messageElement);
        }, 500);
    }

    return messageElement;
}

function setupLongpressListener(messageElement) {
    let longpressTimer = null;
    
    messageElement.addEventListener('touchstart', (e) => {
        if (e.touches.length === 1) {
            longpressTimer = setTimeout(() => {
                showLongpressMenu(e, messageElement);
            }, 500);
        }
    });

    messageElement.addEventListener('touchend', () => {
        if (longpressTimer) {
            clearTimeout(longpressTimer);
            longpressTimer = null;
        }
    });

    messageElement.addEventListener('touchmove', () => {
        if (longpressTimer) {
            clearTimeout(longpressTimer);
            longpressTimer = null;
        }
    });
}

function showLongpressMenu(event, element) {
    event.preventDefault();
    
    const menu = document.getElementById('longpressMenu') || createLongpressMenu();
    const rect = element.getBoundingClientRect();
    
    menu.style.left = `${Math.min(rect.left, window.innerWidth - 140)}px`;
    menu.style.top = `${rect.top}px`;
    menu.classList.add('show');
    
    menu.dataset.targetId = element.closest('.message').dataset.id;
}

function createLongpressMenu() {
    const menu = document.createElement('div');
    menu.id = 'longpressMenu';
    menu.className = 'longpress-menu';
    menu.innerHTML = `
        <div class="longpress-menu-item copy" onclick="copyMessage(this)">📋 复制</div>
        <div class="longpress-menu-item retract" onclick="retractMessage(this)">↩️ 撤回</div>
    `;
    document.body.appendChild(menu);
    
    document.addEventListener('click', (e) => {
        if (!menu.contains(e.target)) {
            menu.classList.remove('show');
        }
    });
    
    return menu;
}

function copyMessage(menuItem) {
    const menu = menuItem.closest('.longpress-menu');
    const msgId = menu.dataset.targetId;
    const message = document.querySelector(`.message[data-id="${msgId}"]`);
    const bubble = message?.querySelector('.message-bubble');
    
    if (bubble) {
        const text = bubble.textContent || '';
        navigator.clipboard.writeText(text).then(() => {
            showToast('已复制到猫抓板 📋');
        }).catch(() => {
            showToast('复制失败');
        });
    }
    
    menu.classList.remove('show');
}

function retractMessage(menuItem) {
    const menu = menuItem.closest('.longpress-menu');
    const msgId = menu.dataset.targetId;
    const message = document.querySelector(`.message[data-id="${msgId}"]`);
    
    if (message) {
        message.style.opacity = '0';
        message.style.transform = 'scale(0.8)';
        message.style.transition = 'all 0.3s';
        
        setTimeout(() => {
            message.remove();
            showToast('🧹 你清理了一根猫毛');
        }, 300);
    }
    
    menu.classList.remove('show');
}

function updateMessageStatus(msgId, status) {
    const statusEl = document.getElementById(`msg-status-${msgId}`);
    if (!statusEl) return;

    const statusIcons = {
        sending: '<span>⏳</span>',
        failed: '<span class="message-status-failed" onclick="resendMessage(\'' + msgId + '\')">❗</span>',
        success: '<span>✓✓</span>'
    };
    
    statusEl.className = `message-status ${status}`;
    statusEl.innerHTML = statusIcons[status] || '';
}

function resendMessage(msgId) {
    const message = document.querySelector(`.message[data-id="${msgId}"]`);
    if (!message) return;
    
    const bubble = message.querySelector('.message-bubble');
    const text = bubble.textContent || '';
    
    if (!text || !isConnected) return;
    
    updateMessageStatus(msgId, 'sending');
    
    websocket.send(JSON.stringify({
        type: 'message',
        text: text,
        context: ''
    }));
}

function formatMessageContent(content) {
    let html = escapeHtml(content);

    html = html.replace(/\n/g, '<br>');

    html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*(.*?)\*/g, '<em>$1</em>');
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');

    html = html.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, '<img src="$2" alt="$1" class="message-image">');

    return html;
}

function addSystemMessage(content) {
    const container = document.getElementById('messagesContainer');
    const messageElement = document.createElement('div');
    messageElement.className = 'system-message';
    messageElement.textContent = content;

    container.insertBefore(messageElement, document.getElementById('typingIndicator'));
    scrollToBottom();
}

function addProactiveMessage(content) {
    const container = document.getElementById('messagesContainer');
    const messageElement = document.createElement('div');
    messageElement.className = 'message ai';
    messageElement.innerHTML = `
        <div class="message-avatar">🐱</div>
        <div class="message-content">
            <div class="message-username">喵艺</div>
            <div class="message-bubble" style="border-left: 3px solid #ff9a9e;">
                💡 ${escapeHtml(content)}
            </div>
            <div class="message-time">${formatTime(Date.now())}</div>
        </div>
    `;

    container.insertBefore(messageElement, document.getElementById('typingIndicator'));
    scrollToBottom();
    playNotificationSound();
}

function addThoughtBubble(content, timestamp) {
    const container = document.getElementById('thoughtBubblesContainer');
    if (!container) return;

    const bubbleElement = document.createElement('div');
    bubbleElement.className = 'thought-bubble-item';
    const timeStr = timestamp ? formatTime(timestamp) : formatTime(Date.now());

    bubbleElement.innerHTML = `
        <div class="thought-bubble-content">${escapeHtml(content)}</div>
        <div class="thought-bubble-time">${timeStr}</div>
    `;

    container.insertBefore(bubbleElement, container.firstChild);

    const bubbles = container.querySelectorAll('.thought-bubble-item');
    if (bubbles.length > 3) {
        bubbles[bubbles.length - 1].remove();
    }
}

function updateThoughtBubbles(bubbles) {
    const container = document.getElementById('thoughtBubblesContainer');
    if (!container) return;

    if (!bubbles || bubbles.length === 0) {
        container.innerHTML = '<p style="color: var(--text-muted); font-size: 12px; text-align: center; font-style: italic;">喵艺正在整理思绪...</p>';
        return;
    }

    container.innerHTML = bubbles.map(bubble => `
        <div class="thought-bubble-item">
            <div class="thought-bubble-content">${escapeHtml(bubble.content)}</div>
            <div class="thought-bubble-time">${bubble.time_str}</div>
        </div>
    `).join('');
}

function addThinkingStatus(content) {
    const container = document.getElementById('messagesContainer');
    const statusElement = document.createElement('div');
    statusElement.className = 'thinking-status-card';
    statusElement.innerHTML = `<span>🔍</span><span>${escapeHtml(content)}</span>`;
    statusElement.id = 'thinkingStatusCard';

    container.insertBefore(statusElement, document.getElementById('typingIndicator'));
    scrollToBottom();
}

function removeThinkingStatus() {
    const card = document.getElementById('thinkingStatusCard');
    if (card) card.remove();
}

function addThinkingStep(step, content, totalSteps) {
    const panel = document.getElementById('thinkingSteps');
    if (!panel) return;

    const stepElement = document.createElement('div');
    stepElement.className = 'thinking-step';
    stepElement.innerHTML = `
        <div class="thinking-step-header">
            <span>步骤 ${step}/${totalSteps || '?'}</span>
            <span class="thinking-toggle">▼</span>
        </div>
        <div class="thinking-step-content">${escapeHtml(content)}</div>
    `;

    stepElement.addEventListener('click', () => {
        stepElement.classList.toggle('expanded');
        const toggle = stepElement.querySelector('.thinking-toggle');
        toggle.textContent = stepElement.classList.contains('expanded') ? '▲' : '▼';
    });

    panel.appendChild(stepElement);
    panel.scrollTop = panel.scrollHeight;
}

function clearThinkingSteps() {
    const panel = document.getElementById('thinkingSteps');
    if (panel) {
        panel.innerHTML = '<p style="text-align: center; color: var(--text-muted); padding: 40px;">喵艺正在思考...</p>';
    }
}

function updateEmotion(emotion) {
    const emoji = emotion.emoji || '😊';
    const emojiEl = document.getElementById('emotionEmoji');
    if (emojiEl) emojiEl.textContent = emoji;

    const descriptions = {
        'excited': '像兔子一样蹦蹦跳跳～', 'affectionate': '心像棉花糖一样柔软～',
        'joy': '像被阳光晒过的棉被一样温暖～', 'sadness': '心情像下雨天的窗户...',
        'anger': '尾巴有点炸毛了...', 'love': '心里甜甜的，像吃了草莓蛋糕～',
        'curiosity': '歪着头好奇地看着你～', 'neutral': '平静得像午后的微风～',
        'amusement': '忍不住想笑出声～', 'fatigue': '有点困了，想打个哈欠～',
        'loneliness': '想让你多陪陪我～', 'playfulness': '调皮地吐舌头～',
        'boredom': '有点无聊，想找你玩～', 'hope': '像春天的嫩芽一样充满希望～',
        'gratitude': '心里暖暖的，谢谢你～', 'warmth': '像泡在温水里一样舒服～',
        'wonder': '眼睛亮晶晶的～', 'empathy': '我懂你的感受～',
    };
    const description = emotion.description || descriptions[emotion.dominant_emotion] || '心情平静';
    const descEl = document.getElementById('emotionDescription');
    if (descEl) descEl.textContent = description;

    const intimacy = emotion.intimacy || 0;
    const intimacyBar = document.getElementById('intimacyBar');
    const intimacyValue = document.getElementById('intimacyValue');
    if (intimacyBar) intimacyBar.style.width = `${intimacy}%`;
    if (intimacyValue) intimacyValue.textContent = `${intimacy}/100`;

    const emotionColors = {
        'excited': '#FFD700', 'affectionate': '#FF69B4', 'joy': '#87CEEB',
        'sadness': '#6B8E23', 'anger': '#FF6347', 'love': '#FF69B4',
        'curiosity': '#9370DB', 'neutral': '#D3D3D3', 'amusement': '#FFA500',
        'fatigue': '#A9A9A9', 'loneliness': '#4169E1', 'playfulness': '#FFB6C1',
        'boredom': '#708090', 'hope': '#98FB98', 'gratitude': '#F0E68C',
        'warmth': '#FFE4B5', 'wonder': '#E6E6FA', 'empathy': '#ADD8E6',
    };
    const color = emotionColors[emotion.dominant_emotion] || '#D3D3D3';
    if (emojiEl) emojiEl.style.color = color;

    const thoughtEl = document.getElementById('currentThought');
    if (thoughtEl) thoughtEl.textContent = emotion.thought || '喵艺正在思考...';
}

function updateEmotionBars(emotionalState) {
    if (!emotionalState) return;

    const container = document.getElementById('emotionBars');
    const summary = document.getElementById('emotionSummary');
    if (!container) return;

    const emotions = {
        joy: emotionalState.joy || 0,
        sadness: emotionalState.sadness || 0,
        anger: emotionalState.anger || 0,
        love: emotionalState.love || 0,
        excitement: emotionalState.excitement || 0,
        calm: emotionalState.calm || 0
    };

    const sorted = Object.entries(emotions)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 4);

    container.innerHTML = sorted.map(([name, value]) => {
        const percent = Math.round(value * 100);
        const labels = {
            joy: '开心', sadness: '难过', anger: '生气',
            love: '爱意', excitement: '兴奋', calm: '平静'
        };
        return `
            <div class="emotion-bar-row">
                <span class="emotion-bar-label">${labels[name] || name}</span>
                <div class="emotion-bar-track">
                    <div class="emotion-bar-fill ${name}" style="width: ${percent}%"></div>
                </div>
                <span class="emotion-bar-value">${percent}%</span>
            </div>
        `;
    }).join('');

    const dominant = emotionalState.dominant_emotion || 'calm';
    const descriptions = {
        joy: '心情像被阳光晒过的棉被～',
        sadness: '心情像下雨天的窗户...',
        anger: '心里有一团小火苗在烧...',
        love: '心里甜甜的，像吃了草莓蛋糕～',
        excitement: '超级兴奋，尾巴摇个不停！',
        calm: '像一片安静的湖水～'
    };
    if (summary) {
        summary.textContent = descriptions[dominant] || '心情平静';
    }
}

function updateMemories(memories) {
    const container = document.getElementById('memoriesList');
    if (!container) return;

    if (!memories || memories.length === 0) {
        container.innerHTML = '<div class="memory-item">暂无记忆</div>';
        return;
    }

    container.innerHTML = memories.slice(0, 5).map(mem => `
        <div class="memory-item">
            <span style="color: #ff9a9e; margin-right: 4px;">📝</span>
            ${escapeHtml(mem)}
        </div>
    `).join('');
}

function showTypingIndicator() {
    document.getElementById('typingIndicator').classList.add('show');
    hideStatusIndicator();
    scrollToBottom();
}

function hideTypingIndicator() {
    document.getElementById('typingIndicator').classList.remove('show');
    removeThinkingStatus();
}

function updateStatusIndicator(status, content) {
    const indicator = document.getElementById('statusIndicator');
    const icon = document.getElementById('statusIcon');
    const text = document.getElementById('statusText');

    const statusConfig = {
        'thinking': { icon: '🤔', text: '正在分析情感...' },
        'searching': { icon: '🔍', text: '正在搜索网络知识...' },
        'verifying': { icon: '✅', text: '正在核实信息...' },
        'typing': { icon: '⌨️', text: '正在组织语言...' },
    };

    const config = statusConfig[status] || { icon: '🤔', text: content };

    icon.textContent = config.icon;
    text.textContent = content || config.text;
    indicator.classList.add('show');
    scrollToBottom();
}

function hideStatusIndicator() {
    document.getElementById('statusIndicator').classList.remove('show');
}

const QUICK_STICKERS = [
    '😊', '😂', '❤️', '👍', '😢', '🤔', '😎', '🎉', '🥰', '😭',
    '💪', '🙏', '🤗', '😴', '🤣', '😤', '🥺', '😍', '🫂', '✨',
];

function initQuickStickers() {
    const container = document.getElementById('quickStickers');
    if (!container) return;

    QUICK_STICKERS.forEach(sticker => {
        const btn = document.createElement('button');
        btn.className = 'quick-sticker-btn';
        btn.textContent = sticker;
        btn.title = sticker;
        btn.onclick = () => insertSticker(sticker);
        container.appendChild(btn);
    });
}

function insertSticker(sticker) {
    const input = document.getElementById('messageInput');
    const start = input.selectionStart;
    const end = input.selectionEnd;
    const text = input.value;
    input.value = text.substring(0, start) + sticker + text.substring(end);
    input.selectionStart = input.selectionEnd = start + sticker.length;
    input.focus();
}

function uploadImageSticker() {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/*';
    input.onchange = async (e) => {
        const file = e.target.files[0];
        if (!file) return;

        const formData = new FormData();
        formData.append('file', file);

        try {
            const token = localStorage.getItem('token');
            const response = await fetch(`${API_BASE}/sticker/upload`, {
                method: 'POST',
                headers: { 'Authorization': `Bearer ${token}` },
                body: formData,
            });

            const data = await response.json();
            if (data.code === 200) {
                const imageUrl = window.location.origin + '/stickers/' + data.data.file_url.split('/').pop();
                addMessage('user', `![图片表情](${imageUrl})`);

                websocket.send(JSON.stringify({
                    type: 'message',
                    text: data.data.text,
                    context: ''
                }));

                showTypingIndicator();
            } else {
                showToast(data.message || '上传失败');
            }
        } catch (error) {
            showToast('上传失败，请重试');
        }
    };
    input.click();
}

function initEmojiStickerPanel() {
    const container = document.getElementById('stickerGrid');
    if (!container) return;

    const allEmojis = [
        '😊', '😄', '😆', '😅', '🤣', '😂', '🙂', '🙃', '😉', '😊',
        '😍', '🥰', '😘', '😗', '😚', '😙', '🥲', '😋', '😛', '😜',
        '🤪', '😝', '🤑', '🤗', '🤭', '🤫', '🤔', '🤐', '🤨', '😐',
        '😑', '😶', '😏', '😒', '🙄', '😬', '🤥', '😌', '😔', '😪',
        '🤤', '😴', '😷', '🤒', '🤕', '🤢', '🤮', '🤧', '🥵', '🥶',
        '🥴', '😵', '🤯', '🤠', '🥳', '🥸', '😎', '🤓', '🧐', '😕',
        '😟', '🙁', '☹️', '😮', '😯', '😲', '😳', '🥺', '😦', '😧',
        '😨', '😰', '😥', '😢', '😭', '😱', '😖', '😣', '😞', '😓',
        '😩', '😫', '🥱', '😤', '😡', '😠', '🤬', '😈', '👿', '💀',
        '☠️', '💩', '🤡', '👹', '👺', '👻', '👽', '👾', '🤖', '🎃',
        '🐱', '😺', '😸', '😹', '😻', '😼', '😽', '🙀', '😿', '😾',
    ];

    container.innerHTML = '';

    allEmojis.forEach(emoji => {
        const item = document.createElement('div');
        item.className = 'emoji-sticker-item';
        item.textContent = emoji;
        item.onclick = () => {
            insertSticker(emoji);
            toggleStickerPanel();
        };
        container.appendChild(item);
    });
}

let shouldAutoScroll = true;

function scrollToBottom() {
    const container = document.getElementById('messagesContainer');
    if (!container) return;
    if (shouldAutoScroll) {
        container.scrollTop = container.scrollHeight;
    }
}

/* ========== 智能滚动与回到底部按钮 ========== */
function initScrollToBottom() {
    const container = document.getElementById('messagesContainer');
    const fabBtn = document.getElementById('scrollToBottomBtn');
    if (!container || !fabBtn) return;

    container.addEventListener('scroll', () => {
        const distanceToBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
        if (distanceToBottom > 80) {
            shouldAutoScroll = false;
            fabBtn.classList.add('visible');
        } else if (distanceToBottom < 40) {
            shouldAutoScroll = true;
            fabBtn.classList.remove('visible');
        }
    });

    fabBtn.addEventListener('click', () => {
        container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' });
        shouldAutoScroll = true;
    });
}

/* ========== 骨架屏 ========== */
function showSkeletonLoader() {
    const container = document.getElementById('messagesContainer');
    const skeleton = document.createElement('div');
    skeleton.className = 'message ai message-slide-in skeleton-bubble';
    skeleton.id = 'skeletonLoader';
    skeleton.innerHTML = `
        <div class="message-avatar">🐱</div>
        <div class="message-content">
            <div class="message-username">喵艺</div>
            <div class="message-bubble skeleton-bubble">
                <div class="skeleton-line skeleton-line-1"></div>
                <div class="skeleton-line skeleton-line-2"></div>
                <div class="skeleton-line skeleton-line-3"></div>
            </div>
        </div>
    `;
    container.insertBefore(skeleton, document.getElementById('typingIndicator'));
    scrollToBottom();
}

function hideSkeletonLoader() {
    const skeleton = document.getElementById('skeletonLoader');
    if (skeleton) skeleton.remove();
}

/* ========== WebSocket 重连提示 ========== */
function showWsAlert() {
    const alert = document.getElementById('wsAlert');
    if (alert) alert.classList.add('visible');
}

function hideWsAlert() {
    const alert = document.getElementById('wsAlert');
    if (alert) alert.classList.remove('visible');
}

/* ========== 键盘快捷键 ========== */
document.addEventListener('keydown', (e) => {
    // Ctrl+Enter 发送消息
    if (e.ctrlKey && e.key === 'Enter') {
        e.preventDefault();
        sendMessage();
    }
    // Escape 关闭面板
    if (e.key === 'Escape') {
        const stickerPanel = document.getElementById('stickerPanel');
        if (stickerPanel && stickerPanel.classList.contains('show')) {
            stickerPanel.classList.remove('show');
        }
        const settingsModal = document.getElementById('settingsModal');
        if (settingsModal && settingsModal.classList.contains('show')) {
            settingsModal.classList.remove('show');
        }
        const voiceMenu = document.getElementById('voiceMenu');
        if (voiceMenu && voiceMenu.classList.contains('show')) {
            voiceMenu.classList.remove('show');
        }
    }
});

/* ========== 移动端键盘适配 ========== */
if ('visualViewport' in window) {
    let lastViewportHeight = window.visualViewport.height;
    
    const handleViewportResize = () => {
        const diff = lastViewportHeight - window.visualViewport.height;
        
        if (diff > 100) {
            setTimeout(() => {
                const container = document.getElementById('messagesContainer');
                if (container) {
                    container.scrollTop = container.scrollHeight;
                }
            }, 300);
        }
        
        lastViewportHeight = window.visualViewport.height;
    };
    
    window.visualViewport.addEventListener('resize', handleViewportResize);
    window.visualViewport.addEventListener('scroll', handleViewportResize);
} else {
    window.addEventListener('resize', () => {
        const container = document.getElementById('messagesContainer');
        if (container) {
            container.scrollTop = container.scrollHeight;
        }
    });
}

function formatTime(timestamp) {
    const date = new Date(timestamp);
    const hours = date.getHours().toString().padStart(2, '0');
    const minutes = date.getMinutes().toString().padStart(2, '0');
    return `${hours}:${minutes}`;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function sendMessage() {
    const input = document.getElementById('messageInput');
    const text = input.value.trim();

    if (!text || !isConnected) return;

    input.value = '';
    updateSendButtonVisibility();
    
    const messageElement = addMessage('user', text, null, null, null, null, null, null, null, 'sending');
    const msgId = messageElement.dataset.id;

    websocket.send(JSON.stringify({
        type: 'message',
        text: text,
        context: ''
    }));

    setTimeout(() => {
        updateMessageStatus(msgId, 'success');
    }, 500);

    showTypingIndicator();
    clearThinkingSteps();
}

function sendQuickReply(text) {
    document.getElementById('messageInput').value = text;
    sendMessage();
}

document.getElementById('messageInput').addEventListener('keypress', (e) => {
    if (e.key === 'Enter') {
        sendMessage();
    }
});

document.getElementById('messageInput').addEventListener('input', (e) => {
    updateSendButtonVisibility();
});

function updateSendButtonVisibility() {
    const input = document.getElementById('messageInput');
    const sendBtn = document.getElementById('sendBtn');
    if (input && sendBtn) {
        sendBtn.style.display = input.value.trim() ? 'flex' : 'none';
    }
}

async function startRecording() {
    if (isRecording) {
        stopRecording();
        return;
    }

    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        mediaRecorder = new MediaRecorder(stream);
        audioChunks = [];

        mediaRecorder.ondataavailable = (event) => {
            if (event.data.size > 0) {
                audioChunks.push(event.data);
            }
        };

        mediaRecorder.onstop = () => {
            sendAudio();
            stream.getTracks().forEach(track => track.stop());
        };

        mediaRecorder.start();
        isRecording = true;
        updateRecordingUI(true);
        showVoiceStatus('🎙️ 聆听中...');

    } catch (error) {
        console.error('录音失败:', error);
        showToast('无法访问麦克风');
    }
}

function stopRecording() {
    if (mediaRecorder && isRecording) {
        mediaRecorder.stop();
        isRecording = false;
        updateRecordingUI(false);
        showVoiceStatus('🔍 识别中...');
    }
}

function updateRecordingUI(recording) {
    const btn = document.querySelector('.tool-btn[title="语音"]');
    if (recording) {
        btn.classList.add('recording');
        btn.textContent = '⏹️';
    } else {
        btn.classList.remove('recording');
        btn.textContent = '🎤';
    }
}

function showVoiceStatus(text) {
    const status = document.getElementById('voiceStatus');
    status.textContent = text;
    status.style.display = text ? 'flex' : 'none';
}

function sendAudio() {
    if (audioChunks.length === 0) return;

    const blob = new Blob(audioChunks, { type: 'audio/webm' });
    const reader = new FileReader();

    reader.onloadend = () => {
        const base64Audio = reader.result.split(',')[1];

        if (websocket && isConnected) {
            websocket.send(JSON.stringify({
                type: 'audio',
                content: base64Audio
            }));
        }
    };

    reader.readAsDataURL(blob);
}

function addVoiceResult(text) {
    showVoiceStatus('');
    if (text) {
        document.getElementById('messageInput').value = text;
        showToast('语音识别完成');
    } else {
        showToast('无法识别语音');
    }
}

function toggleStickerPanel() {
    const panel = document.getElementById('stickerPanel');
    panel.classList.toggle('show');

    if (panel.classList.contains('show')) {
        loadStickers();
    }
}

async function loadStickers(keyword = '') {
    try {
        const response = await apiFetch(`${API_BASE}/emoticons/search?keyword=${encodeURIComponent(keyword)}`);
        const data = await response.json();

        if (data.code === 200) {
            renderStickers(data.data);
        }
    } catch (error) {
        console.error('加载表情失败:', error);
    }
}

function renderStickers(stickers) {
    const grid = document.getElementById('stickerGrid');
    grid.innerHTML = '';

    stickers.forEach(sticker => {
        const item = document.createElement('div');
        item.className = 'sticker-item';
        item.innerHTML = `<img src="${sticker}" alt="表情">`;
        item.addEventListener('click', () => {
            sendSticker(sticker);
        });
        grid.appendChild(item);
    });
}

function searchStickers() {
    const keyword = document.getElementById('stickerSearch').value;
    loadStickers(keyword);
}

function sendSticker(url) {
    if (!isConnected) return;

    addMessage('user', `![表情](${url})`);

    websocket.send(JSON.stringify({
        type: 'sticker',
        content: url
    }));

    toggleStickerPanel();
    showTypingIndicator();
}

function createCustomSticker() {
    document.getElementById('customStickerModal').classList.add('show');
}

function closeCustomStickerModal() {
    document.getElementById('customStickerModal').classList.remove('show');
}

document.querySelectorAll('.emotion-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.emotion-btn').forEach(b => b.classList.remove('selected'));
        btn.classList.add('selected');
        selectedEmotion = btn.dataset.emotion;
    });
});

async function generateCustomSticker() {
    const text = document.getElementById('customStickerText').value.trim();

    if (!text) {
        showToast('请输入文字内容');
        return;
    }

    try {
        const response = await apiFetch(`${API_BASE}/emoticons/create`, {
            method: 'POST',
            body: { text, emotion: selectedEmotion }
        });

        const data = await response.json();

        if (data.code === 200) {
            addMessage('user', `\`\`\`\n${data.data.text}\n\`\`\``);

            if (websocket && isConnected) {
                websocket.send(JSON.stringify({
                    type: 'custom_sticker',
                    content: data.data.text
                }));
            }

            closeCustomStickerModal();
            document.getElementById('customStickerText').value = '';
            showTypingIndicator();
        } else {
            showToast(data.message || '生成失败');
        }
    } catch (error) {
        showToast('网络连接失败');
    }
}

let _currentPanel = null;

function switchPanel(panelId) {
    const sidePanel = document.getElementById('sidePanel');
    if (!sidePanel) return;

    // Toggle 逻辑：如果当前面板已打开且点击的是同一个标签，则关闭
    if (_currentPanel === panelId && sidePanel.classList.contains('expanded')) {
        closeSidePanel();
        return;
    }

    document.querySelectorAll('.panel-tab').forEach(tab => tab.classList.remove('active'));
    document.querySelectorAll('.panel-icon-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.panel-content').forEach(content => content.classList.remove('active'));

    const iconBtn = document.querySelector(`.panel-icon-btn[onclick="switchPanel('${panelId}')"]`);
    if (iconBtn) {
        iconBtn.classList.add('active');
    }

    const tabBtn = document.querySelector(`.panel-tab[onclick="switchPanel('${panelId}')"]`);
    if (tabBtn) {
        tabBtn.classList.add('active');
    }

    const panelContent = document.getElementById(`${panelId}Panel`);
    if (panelContent) {
        panelContent.classList.add('active');
    }

    sidePanel.classList.add('expanded');
    _currentPanel = panelId;

    if (panelId === 'growth') {
        loadGrowthData();
    }
}

function closeSidePanel() {
    const sidePanel = document.getElementById('sidePanel');
    if (sidePanel) {
        sidePanel.classList.remove('expanded');
    }
    _currentPanel = null;
}

function openGrowthPanel() {
    showToast('喵~ 小鱼干商城正在建设中...');
}

/* 点击聊天区域关闭侧边栏 */
document.addEventListener('click', (e) => {
    const sidePanel = document.getElementById('sidePanel');
    if (!sidePanel || !sidePanel.classList.contains('expanded')) return;
    
    // 如果点击的是面板内部或面板图标按钮，不关闭
    if (sidePanel.contains(e.target)) return;
    if (e.target.closest('.panel-icon-btn')) return;
    if (e.target.closest('.mobile-nav-btn')) return;
    
    closeSidePanel();
});

function toggleSettings() {
    const modal = document.getElementById('settingsModal');
    if (!modal) return;
    modal.classList.toggle('show');

    if (modal.classList.contains('show') && currentUser) {
        const nameEl = document.getElementById('profileName');
        const roleEl = document.getElementById('profileRole');
        if (nameEl) nameEl.textContent = currentUser.username;
        if (roleEl) roleEl.textContent = currentUser.role === 'admin' ? '管理员' : '普通用户';
    }
}

function showAbout() {
    showToast('喵艺 AI 伴侣 v1.0');
}

async function exportChat() {
    try {
        const response = await apiFetch(`${API_BASE}/export/chat`, {
            method: 'POST',
            body: { format: 'html' }
        });

        if (response.ok) {
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `chat_export_${Date.now()}.html`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);
            showToast('聊天记录已导出');
        } else {
            showToast('导出失败');
        }
    } catch (error) {
        showToast('网络连接失败');
    }
}

async function checkin() {
    try {
        const response = await apiFetch(`${API_BASE}/checkin`);
        const data = await response.json();

        if (data.code === 200) {
            showToast(data.message);
            if (data.data && data.data.streak) {
                showToast(`🎉 已连续签到 ${data.data.streak} 天！`);
            }
            loadGrowthData();
        } else {
            showToast(data.message || '签到失败');
        }
    } catch (error) {
        showToast('签到失败，请重试');
    }
}

async function showMyMemory() {
    try {
        const response = await apiFetch(`${API_BASE}/memory/list`);
        const data = await response.json();

        if (data.code === 200) {
            const memories = data.data || [];
            renderMemoryList(memories);
        } else {
            showToast(data.message || '获取记忆失败');
        }
    } catch (error) {
        showToast('网络连接失败');
    }
}

function renderMemoryList(memories) {
    if (!Array.isArray(memories)) {
        memories = [];
    }
    let html = '<div style="max-height: 300px; overflow-y: auto;">';

    if (memories.length === 0) {
        html += '<p style="text-align: center; color: #999; padding: 20px;">还没有记忆哦~</p>';
    } else {
        memories.forEach((memory, index) => {
            const encodedKey = encodeURIComponent(memory.key || '');
            html += `
                <div style="display: flex; justify-content: space-between; align-items: center; padding: 10px; border-bottom: 1px solid rgba(0,0,0,0.05);">
                    <div style="flex: 1;">
                        <p style="font-size: 14px; margin: 0;">${escapeHtml(memory.key || '')}</p>
                        <p style="font-size: 11px; color: #999; margin: 4px 0 0;">${escapeHtml(memory.value || '')}</p>
                    </div>
                    <button onclick="deleteMemory('${encodedKey}')" style="border: none; background: rgba(231,76,60,0.1); color: #e74c3c; padding: 6px 12px; border-radius: 6px; cursor: pointer; font-size: 12px;">删除</button>
                </div>
            `;
        });
    }

    html += '</div>';

    const modal = document.createElement('div');
    modal.className = 'custom-sticker-modal show';
    modal.innerHTML = `
        <div class="modal-content">
            <h3>🧠 我的记忆 (${memories.length})</h3>
            ${html}
            <button class="close-btn" onclick="this.closest('.custom-sticker-modal').remove()">✕</button>
        </div>
    `;

    document.body.appendChild(modal);
}

async function deleteMemory(key) {
    if (!confirm('确定要删除这条记忆吗？')) {
        return;
    }

    try {
        const response = await apiFetch(`${API_BASE}/memory/delete/${key}`, {
            method: 'DELETE'
        });

        const data = await response.json();

        if (data.code === 200) {
            showToast('记忆已删除');
            loadMemoryCards();
            showMyMemory();
        } else {
            showToast(data.message || '删除失败');
        }
    } catch (error) {
        showToast('网络连接失败');
    }
}

async function clearMemory() {
    if (!confirm('确定要清除我的记忆吗？此操作不可恢复！')) {
        return;
    }

    try {
        const response = await apiFetch(`${API_BASE}/memory/clear`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ days: 30 })
        });

        const data = await response.json();

        if (data.code === 200) {
            showToast(data.message || '记忆已清除');
        } else {
            showToast(data.message || '清除失败');
        }
    } catch (error) {
        showToast('网络连接失败');
    }
}

async function changeNickname() {
    const nickname = prompt('你想让喵艺叫你什么？');
    if (nickname) {
        try {
            const response = await apiFetch(`${API_BASE}/memory/save`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ key: '称呼', value: nickname })
            });
            const data = await response.json();
            if (data.code === 200) {
                showToast(`好的，以后就叫你${nickname}啦～`);
            } else {
                showToast('保存失败，请重试');
            }
        } catch (error) {
            showToast('网络连接失败');
        }
    }
}

function showModal(title, content) {
    const modal = document.createElement('div');
    modal.className = 'modal-overlay';
    modal.innerHTML = `
        <div class="modal-content">
            <div class="modal-header">
                <h3>${title}</h3>
                <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">✕</button>
            </div>
            <div class="modal-body">${content}</div>
        </div>
    `;
    modal.addEventListener('click', function(e) {
        if (e.target === modal) modal.remove();
    });
    document.body.appendChild(modal);
}

function playLevelUpAnimation(newLevel) {
    const toast = document.createElement('div');
    toast.className = 'level-up-toast';
    toast.innerHTML = `🎉 升级啦！Lv.${newLevel}`;
    document.body.appendChild(toast);
    setTimeout(function() { toast.remove(); }, 3000);
}

function showLevelDetail() {
    const levelEl = document.getElementById('growthLevel');
    const expBar = document.getElementById('expProgressBar');
    const level = levelEl ? levelEl.textContent : 'Lv.1';
    const exp = expBar ? expBar.style.width : '0%';
    showModal('🎖️ 等级详情',
        '<p>当前等级：' + level + '</p>' +
        '<p>经验进度：' + exp + '</p>' +
        '<p>继续聊天就能升级哦～每次对话都会增加经验值！</p>'
    );
}

function showIntimacyDetail() {
    const intimacyEl = document.getElementById('growthIntimacy');
    const intimacy = intimacyEl ? parseInt(intimacyEl.textContent) || 0 : 0;
    const stage = getIntimacyStage(intimacy);
    showModal('❤️ 亲密度详情',
        '<p>当前亲密度：' + intimacy + '</p>' +
        '<p>关系阶段：' + stage + '</p>' +
        '<p>多和我聊天、分享心情、发送表情，我们的关系会越来越近哦～</p>'
    );
}

function showAchievements() {
    const achEl = document.getElementById('growthAchievements');
    const text = achEl ? achEl.textContent : '0个';
    showModal('🏆 成就列表',
        '<p>当前成就：' + text + '</p>' +
        '<p>每次升级都会解锁新成就哦～继续努力吧！</p>'
    );
}

function getToken() {
    return localStorage.getItem('token');
}

function showAllMemories() {
    apiFetch(API_BASE + '/memory/list')
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.code === 200 && data.data && data.data.length > 0) {
            let list = '';
            data.data.forEach(function(m) {
                list += '<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--border-color)">' +
                    '<div><strong>' + escapeHtml(m.key) + '</strong>: ' + escapeHtml(m.value) + '</div>' +
                    '<button onclick="deleteMemory(\'' + escapeHtml(m.key) + '\')" style="background:none;border:none;color:var(--danger-color);cursor:pointer;font-size:14px">删除</button>' +
                '</div>';
            });
            showModal('🧠 全部记忆', list);
        } else {
            showModal('🧠 全部记忆', '<p>还没有任何记忆～多和我聊天，我会记住关于你的事哦！</p>');
        }
    })
    .catch(function() {
        showModal('🧠 全部记忆', '<p>加载失败，请重试</p>');
    });
}

function loadMemoryCards() {
    apiFetch(API_BASE + '/memory/list')
    .then(function(r) { return r.json(); })
    .then(function(data) {
        const container = document.getElementById('memoryList');
        if (!container) return;
        if (data.code === 200 && data.data && data.data.length > 0) {
            const memories = data.data.slice(0, 3);
            let html = '';
            memories.forEach(function(m) {
                html += '<div class="memory-card"><span>' + escapeHtml(m.key) + '</span>' +
                    '<button onclick="deleteMemory(\'' + escapeHtml(m.key) + '\')" class="memory-delete-btn">×</button></div>';
            });
            container.innerHTML = html;
        } else {
            container.innerHTML = '<p style="color:var(--text-muted);font-size:12px;text-align:center">暂无记忆</p>';
        }
    })
    .catch(function() {
        const container = document.getElementById('memoryList');
        if (container) container.innerHTML = '<p style="color:var(--text-muted);font-size:12px;text-align:center">加载失败</p>';
    });
}

function logout() {
    localStorage.removeItem('token');
    currentUser = null;
    if (websocket) {
        websocket.close();
        websocket = null;
    }
    updateAdminUI();
    init();
    showToast('已退出登录');
}

const THEMES = [
    { id: 'warm', name: '☀️ 简约', icon: '☀️' },
    { id: 'cute', name: '🐱 可爱', icon: '🐱' },
    { id: 'dark', name: '🌙 深色', icon: '🌙' },
];

function toggleTheme() {
    const body = document.body;
    const icon = document.getElementById('themeIcon');

    const currentTheme = localStorage.getItem('theme') || 'warm';
    const currentIndex = THEMES.findIndex(t => t.id === currentTheme);
    const nextTheme = THEMES[(currentIndex + 1) % THEMES.length];

    body.classList.remove('theme-warm', 'theme-cute', 'theme-dark', 'dark');

    body.classList.add(`theme-${nextTheme.id}`);

    if (icon) icon.textContent = nextTheme.icon;
    localStorage.setItem('theme', nextTheme.id);
    const statusThemeEl = document.getElementById('statusTheme');
    if (statusThemeEl) statusThemeEl.textContent = nextTheme.name;
    showToast(`已切换到 ${nextTheme.name}`);
}

function showToast(message) {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.classList.add('show');

    setTimeout(() => {
        toast.classList.remove('show');
    }, 3000);
}

function playNotificationSound() {
    try {
        const AudioContext = window.AudioContext || window.webkitAudioContext;
        if (AudioContext) {
            const audioContext = new AudioContext();
            const oscillator = audioContext.createOscillator();
            const gainNode = audioContext.createGain();

            oscillator.connect(gainNode);
            gainNode.connect(audioContext.destination);

            oscillator.frequency.value = 800;
            oscillator.type = 'sine';

            gainNode.gain.setValueAtTime(0.3, audioContext.currentTime);
            gainNode.gain.exponentialRampToValueAtTime(0.01, audioContext.currentTime + 0.5);

            oscillator.start(audioContext.currentTime);
            oscillator.stop(audioContext.currentTime + 0.5);
        }
    } catch (e) {
        console.warn('Notification sound failed:', e);
    }
}

let currentAudio = null;

function playAudio(url) {
    try {
        if (currentAudio) {
            currentAudio.pause();
            currentAudio.currentTime = 0;
            const prevMessages = document.querySelectorAll('.message.ai');
            for (const msg of prevMessages) {
                const prevWave = msg.querySelector('.voice-wave');
                if (prevWave) prevWave.classList.remove('playing');
            }
            currentAudio = null;
        }

        const audio = new Audio(url);
        currentAudio = audio;
        const messages = document.querySelectorAll('.message.ai');
        const lastMessage = messages[messages.length - 1];

        if (lastMessage) {
            let waveElement = lastMessage.querySelector('.voice-wave');
            if (!waveElement) {
                waveElement = document.createElement('div');
                waveElement.className = 'voice-wave';
                waveElement.innerHTML = `
                    <span></span><span></span><span></span><span></span><span></span>
                `;
                const bubble = lastMessage.querySelector('.message-bubble');
                if (bubble) {
                    bubble.appendChild(waveElement);
                }
            }
            waveElement.classList.add('playing');

            audio.onended = () => {
                waveElement.classList.remove('playing');
                currentAudio = null;
            };

            audio.onerror = () => {
                waveElement.classList.remove('playing');
                currentAudio = null;
            };
        } else {
            audio.onended = () => {
                currentAudio = null;
            };
        }

        audio.play().catch(e => {
            console.warn('Audio play failed:', e);
            currentAudio = null;
            if (lastMessage) {
                const waveElement = lastMessage.querySelector('.voice-wave');
                if (waveElement) waveElement.classList.remove('playing');
            }
        });
    } catch (e) {
        console.warn('Play audio failed:', e);
        currentAudio = null;
    }
}

function isMuted() {
    return localStorage.getItem('muted') === 'true';
}

function toggleMute() {
    const muted = !isMuted();
    localStorage.setItem('muted', muted);
    updateMuteButton();
    return muted;
}

function updateMuteButton() {
    const btn = document.getElementById('muteBtn');
    if (btn) {
        btn.innerHTML = isMuted() ? '🔇' : '🔊';
        btn.title = isMuted() ? '点击开启声音' : '点击关闭声音';
    }
}

async function playVoice(voiceText, waveElement = null) {
    if (!voiceText) return;

    if (waveElement) {
        waveElement.classList.add('playing');
    }

    try {
        const response = await apiFetch(`${API_BASE}/voice/synthesize`, {
            method: 'POST',
            body: { text: voiceText }
        });

        const data = await response.json();

        if (data.code === 200 && data.data && data.data.audio_url) {
            if (currentAudio) {
                currentAudio.pause();
                currentAudio.currentTime = 0;
                const allWaves = document.querySelectorAll('.voice-wave');
                allWaves.forEach(w => w.classList.remove('playing'));
                if (waveElement) {
                    waveElement.classList.add('playing');
                }
            }

            const audio = new Audio(data.data.audio_url);
            currentAudio = audio;

            audio.onended = () => {
                if (waveElement) waveElement.classList.remove('playing');
                currentAudio = null;
            };

            audio.onerror = () => {
                if (waveElement) waveElement.classList.remove('playing');
                currentAudio = null;
            };

            await audio.play().catch(e => {
                console.warn('Audio play blocked:', e);
                if (waveElement) waveElement.classList.remove('playing');
                currentAudio = null;
            });
        }
    } catch (error) {
        console.warn('Voice synthesis error:', error);
        if (waveElement) waveElement.classList.remove('playing');
    }
}

async function toggleMessageAudio(btn) {
    const message = btn.closest('.message');
    const voiceText = message.dataset.voiceText;

    if (!voiceText) {
        showToast('没有语音内容');
        return;
    }

    const wave = btn.querySelector('.voice-wave');

    if (wave.classList.contains('playing')) {
        if (currentAudio) {
            currentAudio.pause();
            currentAudio = null;
        }
        wave.classList.remove('playing');
        return;
    }

    await playVoice(voiceText, wave);
}

async function autoPlayAudio(messageElement) {
    const voiceText = messageElement.dataset.voiceText;
    if (!voiceText) return;

    const wave = messageElement.querySelector('.voice-wave');
    await playVoice(voiceText, wave);
}

async function loadGrowthData() {
    try {
        const response = await apiFetch(`${API_BASE}/growth`);
        const data = await response.json();

        if (data.code === 200) {
            updateGrowthData(data.data);
        }
    } catch (error) {
        console.error('加载成长数据失败:', error);
    }
}

function updateGrowthData(data) {
    const level = data.level || 1;
    const exp = data.exp || 0;
    const maxExp = data.max_exp || 100;
    const intimacy = data.intimacy || 0;

    const levelNumEl = document.getElementById('levelNum');
    if (levelNumEl) levelNumEl.textContent = level;
    const expBarEl = document.getElementById('expBar');
    if (expBarEl) expBarEl.style.width = `${(exp / maxExp) * 100}%`;
    const expTextEl = document.getElementById('expText');
    if (expTextEl) expTextEl.textContent = `${exp}/${maxExp}`;

    const intimacyStage = getIntimacyStage(intimacy);
    const intimacyValueEl = document.getElementById('intimacyValue');
    if (intimacyValueEl) intimacyValueEl.textContent = intimacy;
    const intimacyStageEl = document.getElementById('intimacyStage');
    if (intimacyStageEl) intimacyStageEl.textContent = intimacyStage;
    const intimacyBarEl = document.getElementById('intimacyBar');
    if (intimacyBarEl) intimacyBarEl.style.width = `${Math.min(intimacy, 100)}%`;

    const title = getTitle(level);
    const titleBadgeEl = document.getElementById('titleBadge');
    if (titleBadgeEl) titleBadgeEl.textContent = title;

    renderAchievements(data.achievements || []);

    updateStatusBar({
        level: level,
        intimacy: intimacy,
        emoji: document.getElementById('emotionEmoji').textContent
    });

    const growthLevelEl = document.getElementById('growthLevel');
    if (growthLevelEl) growthLevelEl.textContent = 'Lv.' + level;

    const expProgressBar = document.getElementById('expProgressBar');
    if (expProgressBar) expProgressBar.style.width = `${(exp / maxExp) * 100}%`;

    const growthIntimacyEl = document.getElementById('growthIntimacy');
    if (growthIntimacyEl) growthIntimacyEl.textContent = intimacy;

    const achCount = (data.achievements || []).filter(function(a) { return a.unlocked; }).length;
    const growthAchEl = document.getElementById('growthAchievements');
    if (growthAchEl) growthAchEl.textContent = achCount + '个';

    loadMemoryCards();
}

function updateStatusBar(data) {
    const statusBar = document.getElementById('statusBar');
    if (!statusBar) return;

    if (data.level !== undefined) {
        const el = document.getElementById('statusLevel');
        if (el) el.textContent = data.level;
    }
    if (data.intimacy !== undefined) {
        const el = document.getElementById('statusIntimacy');
        if (el) el.textContent = data.intimacy;
        const relEl = document.getElementById('statusRelation');
        if (relEl) relEl.textContent = getIntimacyStage(data.intimacy);
    }
    if (data.emoji !== undefined) {
        const el = document.getElementById('statusEmoji');
        if (el) el.textContent = data.emoji;
    }
    if (data.status !== undefined) {
        const el = document.getElementById('statusText');
        if (el) el.textContent = data.status;
    }
    if (data.theme !== undefined) {
        const el = document.getElementById('statusTheme');
        if (el) el.textContent = data.theme;
    }
}

function getIntimacyStage(intimacy) {
    if (intimacy >= 80) return '亲人';
    if (intimacy >= 65) return '挚友';
    if (intimacy >= 45) return '好朋友';
    if (intimacy >= 25) return '朋友';
    if (intimacy >= 10) return '点头之交';
    return '陌生人';
}

function getTitle(level) {
    if (level >= 50) return '传说喵星人';
    if (level >= 30) return '喵星大师';
    if (level >= 20) return '资深喵友';
    if (level >= 10) return '喵星伙伴';
    if (level >= 5) return '新晋喵友';
    return '初来乍到';
}

function renderAchievements(unlocked) {
    const grid = document.getElementById('achievementsGrid');
    grid.innerHTML = '';

    ACHIEVEMENTS.forEach(achievement => {
        const item = document.createElement('div');
        item.className = 'achievement-badge';

        if (unlocked.includes(achievement.id)) {
            item.textContent = achievement.icon;
            item.title = achievement.name;
        } else {
            item.textContent = '🔒';
            item.style.opacity = '0.3';
        }

        grid.appendChild(item);
    });
}

if ('visualViewport' in window) {
    window.visualViewport.addEventListener('resize', () => {
        scrollToBottom();
    });
}

init();

const savedTheme = localStorage.getItem('theme') || 'warm';
const themeConfig = THEMES.find(t => t.id === savedTheme) || THEMES[0];
const icon = document.getElementById('themeIcon');

document.body.classList.remove('theme-warm', 'theme-cute', 'theme-dark', 'dark');

document.body.classList.add(`theme-${savedTheme}`);

if (icon) {
    icon.textContent = themeConfig.icon;
}