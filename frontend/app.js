// Truva - Wizard Designer Chat Interface

document.addEventListener('DOMContentLoaded', () => {
    const chatMessages = document.getElementById('chat-messages');
    const userInput = document.getElementById('user-input');
    const sendButton = document.getElementById('send-button');
    const suggestions = document.getElementById('copilotkit-footer');
    const agentLanding = document.getElementById('agent-landing');
    const landingFeatured = document.getElementById('landing-featured');
    const attachButton = document.getElementById('attach-button');
    const fileInput = document.getElementById('file-input');
    const voiceButton = document.getElementById('voice-button');
    const stopButton = document.getElementById('stop-button');
    const aiStatus = document.getElementById('ai-status');
    const generatedNumberEl = document.getElementById('generated-number');
    const inputContainer = document.querySelector('.input-container');
    const personaAvatarImg = document.getElementById('persona-avatar-img');
    const AVATAR_STORAGE_KEY = 'assistantAvatar';

    // Conversations state and sidebar list
    const conversationListEl = document.getElementById('conversation-list');
    const CONV_STORAGE_KEY = 'conversations_v1';
    const ACTIVE_CONV_KEY = 'activeConversationId_v1';
    const CONV_INDEX_KEY = 'convIndex_v1';
    let conversations = [];
    let activeConversationId = null;
    let nextConvIndex = 1;
    const landingAvatarImg = document.querySelector('.landing-avatar img');
const defaultAvatar = (personaAvatarImg && personaAvatarImg.src)
    ? personaAvatarImg.src
    : (landingAvatarImg && landingAvatarImg.src) ? landingAvatarImg.src : 'assets/female-2.webp';
// Flag to track whether an assistant response is currently generating
let isGenerating = false;
    // API base configuration: if frontend is served by a static server (e.g., :5500), point to FastAPI backend on :8080
    const API_ORIGIN = window.API_ORIGIN || ((window.location.port === '8080') ? window.location.origin : 'http://127.0.0.1:8080');
    const API_BASE = API_ORIGIN;

    const genId = () => Math.random().toString(36).slice(2, 10);
    const getActiveConv = () => conversations.find(c => c.id === activeConversationId) || null;
    const saveConversations = () => {
        try {
            localStorage.setItem(CONV_STORAGE_KEY, JSON.stringify(conversations));
            localStorage.setItem(ACTIVE_CONV_KEY, activeConversationId || '');
        } catch (e) { /* no-op */ }
    };
    const renderConversationList = () => {
        if (!conversationListEl) return;
        conversationListEl.innerHTML = '';
        conversations.forEach(c => {
            const item = document.createElement('div');
            item.className = `conversation-item MuiPaper-root MuiPaper-elevation MuiPaper-rounded MuiPaper-elevation0 MuiCard-root MuiStack-root css-had6so${c.id===activeConversationId ? ' active' : ''}`;
            item.setAttribute('role','option');
            item.setAttribute('data-id', c.id);
            const left = document.createElement('div');
            left.className = 'left';
            const img = document.createElement('img');
            img.className = 'conv-avatar';
            img.src = c.avatarUrl || defaultAvatar;
            img.alt = 'Conversation Avatar';
            const title = document.createElement('span');
            title.className = 'conv-title';
            title.textContent = c.title || 'New Conversation';
            left.appendChild(img);
            left.appendChild(title);
            const menu = document.createElement('button');
            menu.className = 'icon-btn conv-menu';
            menu.setAttribute('aria-label','Conversation menu');
            menu.title = 'More';
            menu.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" aria-hidden="true" role="img" width="12" height="18" viewBox="0 0 5 24"><path fill="currentColor" d="M5.217 12a2.608 2.608 0 1 1-5.216 0a2.608 2.608 0 0 1 5.216 0m0-9.392a2.608 2.608 0 1 1-5.216 0a2.608 2.608 0 0 1 5.216 0m0 18.783a2.608 2.608 0 1 1-5.216 0a2.608 2.608 0 0 1 5.216 0"></path></svg>';
            item.appendChild(left);
            item.appendChild(menu);
            conversationListEl.appendChild(item);
        });
    };
    const initConversations = () => {
        try {
            const raw = localStorage.getItem(CONV_STORAGE_KEY);
            const saved = raw ? JSON.parse(raw) : [];
            conversations = Array.isArray(saved) ? saved : [];
            const activeSaved = localStorage.getItem(ACTIVE_CONV_KEY);
            activeConversationId = activeSaved || null;
            const idxRaw = localStorage.getItem(CONV_INDEX_KEY);
            nextConvIndex = idxRaw ? (parseInt(idxRaw, 10) || 1) : 1;
            // Normalize titles: remove numbering from default titles
            let changed = false;
            conversations.forEach((c) => {
                const isNumberedDefault = /^(?:New\s+Conversation|Conversation)\s+\d+$/i.test(c.title || '');
                if (isNumberedDefault) {
                    c.title = 'New Conversation';
                    changed = true;
                }
                if (!c.title) {
                    c.title = 'New Conversation';
                    changed = true;
                }
            });
            if (changed) saveConversations();
        } catch (e) { conversations = []; activeConversationId = null; nextConvIndex = 1; }
        if (!conversations.length) {
            const id = genId();
            const conv = { id, title: 'New Conversation', avatarUrl: defaultAvatar, history: [], createdAt: Date.now() };
            conversations.push(conv);
            activeConversationId = id;
            saveConversations();
        }
        // Bind click switching via delegation, with delete support
        if (conversationListEl && !conversationListEl.__bound) {
            conversationListEl.addEventListener('click', (e) => {
                if (typeof isGenerating !== 'undefined' && isGenerating) { try { e.preventDefault(); e.stopPropagation(); } catch(_) {} return; }
                const item = e.target.closest('.conversation-item');
                // Delete button
                if (e.target.closest('.conv-delete')) {
                    const id = item && item.dataset && item.dataset.id;
                    if (id) deleteConversation(id);
                    return;
                }
                // Menu toggle
                if (e.target.closest('.conv-menu')) {
                    if (!item) return;
                    let dropdown = item.querySelector('.conv-dropdown');
                    if (!dropdown) {
                        dropdown = document.createElement('div');
                        dropdown.className = 'conv-dropdown';
                        dropdown.innerHTML = '<button class="dropdown-item rename">Rename</button>\n<button class="dropdown-item delete">Delete</button>';
                        item.appendChild(dropdown);
                    }
                    // Toggle visibility and close other menus
                    const showing = dropdown.classList.toggle('show');
                    if (showing) closeAllConvMenus(item); // close others
                    return;
                }
                // Dropdown actions
                const dd = e.target.closest('.conv-dropdown');
                if (dd) {
                    const id = item && item.dataset && item.dataset.id;
                    if (!id) return;
                    if (e.target.closest('.dropdown-item.delete')) {
                        dd.classList.remove('show');
                        deleteConversation(id);
                        return;
                    }
                    if (e.target.closest('.dropdown-item.rename')) {
                        dd.classList.remove('show');
                        renameConversation(id);
                        return;
                    }
                }
                // Switch conversation when clicking on the item
                if (item && item.dataset && item.dataset.id) {
                    switchConversation(item.dataset.id);
                }
            });
            // Close menus on outside clicks
            document.addEventListener('click', (ev) => {
                if (!ev.target.closest('.conv-dropdown') && !ev.target.closest('.conv-menu')) {
                    closeAllConvMenus();
                }
            });
            conversationListEl.__bound = true;
        }
        renderConversationList();
    };
    const renderChatFromHistory = (history=[]) => {
        chatMessages.innerHTML = '';
        (history || []).forEach(m => {
            addMessageToChat(m.role, m.content, { elapsedMs: m.elapsedMs || null, skipHistory: true });
        });
        chatMessages.scrollTop = chatMessages.scrollHeight;
        // Toggle landing overlay vs chat area based on emptiness
        try {
            const hasHistory = Array.isArray(history) && history.length > 0;
            const chatEl = document.getElementById('chat-messages');
            const inputEl = document.querySelector('.input-container');
            if (hasHistory) {
                chatEl?.classList.remove('hidden');
                if (agentLanding) agentLanding.classList.add('hidden');
                // Ensure input is visible when chat has content
                inputEl?.classList.remove('hidden');
            } else {
                chatEl?.classList.add('hidden');
                if (agentLanding) agentLanding.classList.remove('hidden');
                // Hide input area when landing card is visible
                inputEl?.classList.add('hidden');
            }
        } catch (e) { /* no-op */ }
    };
    const switchConversation = (id) => {
        // Prevent switching conversations while a response is generating
        if (typeof isGenerating !== 'undefined' && isGenerating && id !== activeConversationId) return;
        const conv = conversations.find(c => c.id === id);
        if (!conv) return;
        activeConversationId = id;
        saveConversations();
        renderConversationList();
        chatHistory = (conv.history || []).slice();
        renderChatFromHistory(chatHistory);
        userInput.value = '';
        adjustInputState();
        hideCopilotFooter();
    };

    const deleteConversation = (id) => {
        const idx = conversations.findIndex(c => c.id === id);
        if (idx === -1) return;
        const wasActive = activeConversationId === id;
        conversations.splice(idx, 1);
        if (!conversations.length) {
            activeConversationId = null;
            saveConversations();
            renderConversationList();
            createNewConversation();
            return;
        }
        if (wasActive) {
            const nextIdx = Math.min(idx, conversations.length - 1);
            activeConversationId = conversations[nextIdx].id;
            const conv = conversations.find(c => c.id === activeConversationId);
            chatHistory = (conv && Array.isArray(conv.history) ? conv.history.slice() : []);
            renderChatFromHistory(chatHistory);
            userInput.value = '';
            adjustInputState();
            hideCopilotFooter();
        }
        saveConversations();
        renderConversationList();
        closeAllConvMenus();
    };

    const renameConversation = (id) => {
        const conv = conversations.find(c => c.id === id);
        if (!conv || !conversationListEl) return;
        // Ensure the item exists in DOM
        let item = conversationListEl.querySelector(`.conversation-item[data-id="${id}"]`);
        if (!item) {
            renderConversationList();
            item = conversationListEl.querySelector(`.conversation-item[data-id="${id}"]`);
        }
        if (!item) return;
        const left = item.querySelector('.left');
        if (!left) return;
        const titleEl = item.querySelector('.conv-title');
        const currentTitle = conv.title || 'New Conversation';

        // Create inline input to edit the title
        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'conv-title-input';
        input.value = currentTitle;
        input.setAttribute('aria-label', 'Rename conversation');

        // Replace title span with input (or append if missing)
        if (titleEl) {
            titleEl.replaceWith(input);
        } else {
            left.appendChild(input);
        }

        try { input.focus(); input.select(); } catch (e) {}

        let finished = false;
        const cleanup = (commit) => {
            if (finished) return; finished = true;
            input.removeEventListener('keydown', onKey);
            input.removeEventListener('blur', onBlur);
            if (commit) {
                const next = (input.value || '').trim();
                const words = next.split(/\s+/).filter(Boolean);
                const title = words.slice(0, 4).join(' ');
                if (title.length > 0) {
                    conv.title = title;
                    saveConversations();
                }
            }
            // Re-render to restore normal view
            renderConversationList();
        };
        const onKey = (e) => {
            if (e.key === 'Enter') { e.preventDefault(); cleanup(true); }
            else if (e.key === 'Escape') { e.preventDefault(); cleanup(false); }
        };
        const onBlur = () => { cleanup(true); };
        input.addEventListener('keydown', onKey);
        input.addEventListener('blur', onBlur);
    };

    const closeAllConvMenus = (exceptItem = null) => {
        if (!conversationListEl) return;
        const menus = conversationListEl.querySelectorAll('.conv-dropdown.show');
        menus.forEach(m => {
            if (!exceptItem || m.closest('.conversation-item') !== exceptItem) {
                m.classList.remove('show');
            }
        });
    };

    const createNewConversation = (title) => {
        if (!title) {
            title = 'New Conversation';
        }
        const id = genId();
        const conv = { id, title, avatarUrl: defaultAvatar, history: [], createdAt: Date.now() };
        conversations.push(conv);
        activeConversationId = id;
        saveConversations();
        renderConversationList();
        // Reset chat UI and seed assistant prompt
        chatMessages.innerHTML = '';
        chatHistory = [];
        hideCopilotFooter();
        // Show landing card for empty conversation, hide chat area until first message
        try {
            if (agentLanding) agentLanding.classList.remove('hidden');
            document.getElementById('chat-messages')?.classList.add('hidden');
            // Hide input area while landing card is visible
            document.querySelector('.input-container')?.classList.add('hidden');
        } catch (e) { /* no-op */ }
        
        userInput.value = '';
        adjustInputState();
        try { userInput.focus(); } catch (e) {}
    };

    initConversations();
    // Theme toggle removed per revert request

    // Sidebar collapse + New Chat icon setup
    const sidebar = document.querySelector('.sidebar');
    const sidebarToggle = document.getElementById('sidebar-toggle');
    const SIDEBAR_COLLAPSE_KEY = 'sidebarCollapsed';

    const applySidebarCollapsed = (collapsed) => {
        const bodyEl = document.body;
        // Define toggle icons (expanded vs collapsed)
        const EXPANDED_ICON = '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" aria-hidden="true" role="img" class="component-iconify MuiBox-root css-1t9pz9x iconify iconify--eva" width="1em" height="1em" viewBox="0 0 24 24"><path fill="currentColor" d="M13.83 19a1 1 0 0 1-.78-.37l-4.83-6a1 1 0 0 1 0-1.27l5-6a1 1 0 0 1 1.54 1.28L10.29 12l4.32 5.36a1 1 0 0 1-.78 1.64"></path></svg>';
        const COLLAPSED_ICON = '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" aria-hidden="true" role="img" class="component-iconify MuiBox-root css-16xnpwk iconify iconify--iconamoon" width="1em" height="1em" viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m10 17l5-5m0 0l-5-5"></path></svg>';
        const newChatBtnEl = document.getElementById('new-chat') || document.getElementById('new-chat-btn');
        if (collapsed) {
            bodyEl.classList.add('sidebar-collapsed');
            if (sidebar) sidebar.classList.add('collapsed');
            if (sidebarToggle) {
                sidebarToggle.setAttribute('aria-expanded', 'false');
                sidebarToggle.innerHTML = COLLAPSED_ICON;
            }
            // Hide Add Chat when collapsed
            if (newChatBtnEl) newChatBtnEl.style.display = 'none';
        } else {
            bodyEl.classList.remove('sidebar-collapsed');
            if (sidebar) sidebar.classList.remove('collapsed');
            if (sidebarToggle) {
                sidebarToggle.setAttribute('aria-expanded', 'true');
                sidebarToggle.innerHTML = EXPANDED_ICON;
            }
            // Ensure Add Chat is visible when not collapsed
            if (newChatBtnEl) newChatBtnEl.style.display = 'inline-flex';
        }
        // Recalculate input container position relative to app container
        try { updateInputPosition(); } catch (e) { /* no-op */ }
    };
    try {
        const initialCollapsed = localStorage.getItem(SIDEBAR_COLLAPSE_KEY) === '1';
        applySidebarCollapsed(initialCollapsed);
    } catch (e) {}

    if (sidebarToggle) {
        sidebarToggle.addEventListener('click', () => {
            const isCollapsed = document.body.classList.contains('sidebar-collapsed');
            const next = !isCollapsed;
            applySidebarCollapsed(next);
            // Persist collapsed state
            try { localStorage.setItem(SIDEBAR_COLLAPSE_KEY, next ? '1' : '0'); } catch (e) {}
        });
    }

    // New Chat button: create a new conversation
    const newChatBtn = document.getElementById('new-chat') || document.getElementById('new-chat-btn');
    if (newChatBtn) {
        newChatBtn.addEventListener('click', () => {
            createNewConversation();
        });
    }

    // WebSocket setup for bidirectional streaming
    let ws = null;
    let wsReady = false;
    const wsPending = new Map();
    let wsCounter = 0;

    function initWebSocket() {
        try {
            const wsUrl = (API_ORIGIN.startsWith('https') ? API_ORIGIN.replace('https', 'wss') : API_ORIGIN.replace('http', 'ws')) + '/api/copilot/ws';
            ws = new WebSocket(wsUrl);
            ws.addEventListener('open', () => {
                wsReady = true;
            });
            ws.addEventListener('close', () => {
                wsReady = false;
            });
            ws.addEventListener('error', () => {
                wsReady = false;
            });
            ws.addEventListener('message', (event) => {
                try {
                    const payload = JSON.parse(event.data);
                    const id = payload && payload.id ? String(payload.id) : null;
                    const pending = id ? wsPending.get(id) : null;
                    const text = payload.response || payload.chunk || '';
                    const isDone = payload.done === true || typeof payload.response === 'string';
                    if (pending && pending.streamingDiv) {
                        // Append chunk or finalize
                        if (text) {
                            pending.streamedText += text;
                            pending.streamingDiv.textContent = pending.streamedText;
                        }
                        chatMessages.scrollTop = chatMessages.scrollHeight;
                        if (isDone) {
                            const elapsedMs = pending.startTime ? (Date.now() - pending.startTime) : null;
                            addMessageToChat('assistant', pending.streamedText || text || '');
                            // remove transient container
                            if (pending.streamingContainer && pending.streamingContainer.parentElement) {
                                pending.streamingContainer.remove();
                            }
                            // update generated badge
                            if (generatedNumberEl) {
                                const badge = generatedNumberEl.querySelector('.badge');
                                if (badge) {
                                    badge.textContent = `Generated in ${elapsedMs ? formatElapsed(elapsedMs) : '—'}`;
                                }
                            }
                            wsPending.delete(id);
                        }
                    } else {
                        // No pending; just append assistant message
                        addMessageToChat('assistant', text || '');
                    }
                } catch (e) {
                    // ignore malformed payloads
                }
            });
        } catch (e) { /* no-op */ }
    }
    // initWebSocket disabled

    function sendMessageViaWS(message) { return false; }

    // Initialize chat history
    let chatHistory = [];

    // Persist chatHistory back into the active conversation and refresh the sidebar
    function persistActiveConversationHistory() {
        try {
            const conv = getActiveConv && getActiveConv();
            if (conv) {
                conv.history = (chatHistory || []).slice();
                saveConversations();
                renderConversationList();
            }
        } catch (e) { /* no-op */ }
    }
    // Persist provided history into a specific conversation by id (used to continue generation even after switching)
    function persistConversationHistory(id, history) {
        try {
            const conv = conversations.find(c => c.id === id);
            if (conv) {
                conv.history = (history || []).slice();
                saveConversations();
                renderConversationList();
            }
        } catch (e) { /* no-op */ }
    }

    let lastUserMessage = '';
    let genStartTime = null;
    let recognizing = false;
    let recognition = null;
    let voiceAccumulated = '';
    let baseInputBeforeVoice = '';
    let currentAbortController = null;
    let currentThinkingTimer = null;

    // Load active conversation history into chat
    (function initActiveConversationUI(){
        try {
            const conv0 = getActiveConv && getActiveConv();
            chatHistory = conv0 && Array.isArray(conv0.history) ? conv0.history.slice() : [];
            if (typeof renderChatFromHistory === 'function') {
                renderChatFromHistory(chatHistory);
            }
            // Initialize lastUserMessage from most recent user entry so Regenerate works after refresh
            try {
                const lastUser = Array.isArray(chatHistory) ? [...chatHistory].reverse().find(m => m && m.role === 'user' && typeof m.content === 'string') : null;
                lastUserMessage = lastUser ? (lastUser.content || '') : '';
            } catch (_) { /* no-op */ }
        } catch (e) { /* no-op */ }
    })();

    // Auto-resize textarea and toggle send availability
    function adjustInputState() {
        try {
            // Auto-resize
            userInput.style.height = 'auto';
            const h = Math.min(userInput.scrollHeight, 200);
            userInput.style.height = h + 'px';
        } catch (e) { /* no-op */ }
        // Enable send only if there is content
        sendButton.disabled = !userInput.value.trim();
        // Ensure chat messages are never hidden behind the fixed input container
        try { updateChatPadding(); } catch (e) { /* no-op */ }
    }
    if (userInput) {
        userInput.addEventListener('input', adjustInputState);
        // Initial state
        adjustInputState();
    }

    // Footer positioning: keep footer above the input container
    function updateFooterPosition() {
        try {
            const footer = document.getElementById('copilotkit-footer');
            const ic = document.querySelector('.input-container');
            if (!footer || !ic) return;
            // If footer is embedded inside the input container, don't reposition with bottom
            if (footer.closest('.input-container')) {
                footer.style.bottom = 'auto';
                return;
            }
            const inputHeight = ic.offsetHeight || 0;
            const bottom = Math.max(56, inputHeight + 24);
            footer.style.bottom = bottom + 'px';
        } catch (e) { /* no-op */ }
    }

    // Update chat messages bottom padding to avoid overlap with fixed input
    function updateChatPadding() {
        try {
            const cm = document.getElementById('chat-messages');
            const ic = document.querySelector('.input-container');
            if (!cm || !ic) return;
            const pad = Math.max(100, (ic.offsetHeight || 0) + 24);
            cm.style.paddingBottom = pad + 'px';
        } catch (e) { /* no-op */ }
    }

    // Position input container to span the full width of the app container without overlapping the sidebar
    function updateInputPosition() {
        try {
            const ic = document.querySelector('.input-container');
            if (!ic) return;
            const app = document.querySelector('.app-container');
            if (!app) return;
            const rect = app.getBoundingClientRect();
            ic.style.left = rect.left + 'px';
            ic.style.width = rect.width + 'px';
            ic.style.transform = 'none';
        } catch (e) { /* no-op */ }
    }

    // Hide CopilotKit footer with animation
    function hideCopilotFooter() {
        if (!suggestions) return;
        // Smoothly hide footer suggestions
        suggestions.classList.add('footer-hidden');
        suggestions.classList.remove('footer-show');
        updateToggleButtonState(false);
    }

    // Show CopilotKit footer with animation
    function showCopilotFooter() {
        if (!suggestions) return;
        // Smoothly show footer suggestions
        suggestions.classList.remove('footer-hidden');
        suggestions.classList.add('footer-show');
        updateToggleButtonState(true);
    }

    // Toggle CopilotKit footer visibility
    function toggleCopilotFooter() {
        if (!suggestions) return;
        const isHidden = suggestions.classList.contains('footer-hidden');
        if (isHidden) {
            showCopilotFooter();
        } else {
            hideCopilotFooter();
        }
    }

    // Update toggle button visual state
    function updateToggleButtonState(isVisible) {
        const toggleBtn = document.getElementById('footer-toggle-btn');
        if (!toggleBtn) return;
        
        if (isVisible) {
            toggleBtn.classList.add('footer-visible');
            toggleBtn.classList.remove('footer-hidden');
            toggleBtn.title = 'Hide suggestions';
        } else {
            toggleBtn.classList.add('footer-hidden');
            toggleBtn.classList.remove('footer-visible');
            toggleBtn.title = 'Show suggestions';
        }
    }

    // Assistant avatar persistence
    function loadAssistantAvatar() {
        try {
            const saved = localStorage.getItem(AVATAR_STORAGE_KEY);
            if (saved && personaAvatarImg) {
                personaAvatarImg.src = saved;
            }
        } catch (e) { /* no-op */ }
    }

    // Bind avatar upload controls
    // Upload UI removed per request; keep persistence if a value exists

    // Initial load adjustments
    loadAssistantAvatar();
    // Hide input while landing overlay is visible on initial load
    try {
        if (agentLanding && !agentLanding.classList.contains('hidden')) {
            document.querySelector('.input-container')?.classList.add('hidden');
        }
    } catch (e) { /* no-op */ }

    // Initialize toggle button state based on footer visibility
    try {
        const isFooterHidden = suggestions && suggestions.classList.contains('footer-hidden');
        updateToggleButtonState(!isFooterHidden);
    } catch (e) { /* no-op */ }
    // Agent selection + landing overlay
    let selectedAgent = null;
    function setSuggestionsForAgent(agent) {
        const footer = document.getElementById('copilotkit-footer');
        if (!footer) return;
        const track = footer.querySelector('.footer-carousel-track');
        if (!track) return;
        const groups = {
            agentic: [
                { text: 'Analyze Instagram → brand direction', msg: 'Analyze my Instagram @influencer and suggest a brand direction' },
                { text: 'Pick a brand name + check domains', msg: 'Help me choose a brand name and check domain availability' },
                { text: 'Generate logo ideas', msg: 'Generate three logo ideas for my brand' },
                { text: 'Recommend products to sell', msg: 'Recommend products to sell based on my brand' },
                { text: 'Purple palette with roles', msg: 'Create a purple-themed palette and usage roles' },
            ],
            schedule: [
                { text: 'Check time slots', msg: 'What slots are available next week?' },
                { text: 'Book a call', msg: 'Book a call next week Tuesday 3pm' },
                { text: 'Reschedule meeting', msg: 'Reschedule my meeting to Friday 10am' },
                { text: 'Cancel meeting', msg: 'Cancel my meeting scheduled with Codey' },
            ],
            default: [
                { text: 'Analyze Instagram → brand direction', msg: 'Analyze my Instagram @influencer and suggest a brand direction' },
                { text: 'Pick a brand name + check domains', msg: 'Help me choose a brand name and check domain availability' },
                { text: 'Suggest color palette (HEX)', msg: 'Suggest a color palette with HEX codes for a wellness brand' },
                { text: 'Generate logo ideas', msg: 'Generate three logo ideas for my brand' },
                { text: 'Recommend products to sell', msg: 'Recommend products to sell based on my brand' },
                { text: 'Analyze competitors → differentiation', msg: 'Analyze competitors and propose differentiation' },
                { text: 'Estimate profit', msg: 'Estimate profit if cost is 12, price is 29, units 120' },
                { text: 'Book a call', msg: 'Book a call next week Tuesday 3pm' },
                { text: 'Purple palette with roles', msg: 'Create a purple-themed palette and usage roles' },
            ]
        };
        const items = groups[agent] || groups.default;
        track.innerHTML = '';
        items.forEach(item => {
            const btn = document.createElement('button');
            btn.className = 'suggestion-chip';
            btn.setAttribute('data-msg', item.msg);
            btn.textContent = item.text;
            track.appendChild(btn);
        });
        const clearBtn = document.createElement('button');
        clearBtn.className = 'suggestion-chip danger';
        clearBtn.id = 'clear-chat';
        clearBtn.textContent = 'Clear chat';
        track.appendChild(clearBtn);
    }
    // Initialize suggestions on load even before revealApp
    try {
        setSuggestionsForAgent(selectedAgent || 'default');
    } catch (e) { /* no-op */ }
    // Reveal the main chat UI once the user makes a selection
    function revealApp() {
        try {
            document.getElementById('chat-messages')?.classList.remove('hidden');
            document.getElementById('copilotkit-footer')?.classList.remove('hidden');
            document.getElementById('generated-number')?.classList.remove('hidden');
            document.querySelector('.input-container')?.classList.remove('hidden');
        } catch (_) { /* no-op */ }
        // Ensure suggestions have content even if no agent selected yet
         try {
             setSuggestionsForAgent(selectedAgent || 'default');
         } catch (e) { /* no-op */ }
    }

    function applyAgent(agent) {
        selectedAgent = agent;
        setSuggestionsForAgent(agent);
        // Store in URL for clean navigation
        try {
            const url = new URL(window.location.href);
            if (agent) url.searchParams.set('agent', agent);
            else url.searchParams.delete('agent');
            window.history.replaceState({}, '', url);
        } catch (e) { /* no-op */ }
        // Hide landing overlay when an agent is chosen and reveal chat UI
        if (agentLanding) agentLanding.classList.add('hidden');
        revealApp();
    }
    // Initialize from URL if provided
    try {
        const params = new URLSearchParams(window.location.search);
        const initialAgent = params.get('agent');
        if (initialAgent) {
            applyAgent(initialAgent);
        }

    } catch (e) { /* no-op */ }
    // Bind landing chips
    const DEFAULT_WELCOME_MSG = 'I want to build my brand';
    const AGENT_WELCOME_MSGS = {
        agentic: 'I want to build my brand',
        schedule: 'Please help me schedule a meeting with my team this week.',
        travel: 'Help me plan a trip with flights and hotel options.',
        'content-enrichment': 'Enrich and improve this content for clarity and impact.',
        'marketing-performance': 'Analyze our marketing performance and suggest optimizations.',
        'inbox-calendar': 'Review my inbox and calendar and summarize upcoming priorities.',
        copywriting: 'Draft compelling copy for our new product page.',
        design: 'Design a modern, minimal logo and brand palette.',
        'ugc-video': 'Create a UGC/AI video concept and script.',
        'exec-reporting': 'Prepare an executive report summarizing key metrics.',
        router: 'Route my request to the most suitable agent.',
        'memory-preference': 'Remember my preferences and apply them going forward.'
    };
    document.querySelectorAll('.agent-chip').forEach(chip => {
        chip.addEventListener('click', () => {
            const agent = chip.getAttribute('data-agent');
            applyAgent(agent);
            // Dynamically tailor welcome message for the selected agent
            const msg = AGENT_WELCOME_MSGS[agent] || DEFAULT_WELCOME_MSG;
            userInput.value = msg;
            adjustInputState();
            sendMessage();
        });
    });
    if (landingFeatured) {
        landingFeatured.addEventListener('click', () => {
            if (agentLanding) agentLanding.classList.add('hidden');
            revealApp();
            const idea = landingFeatured.getAttribute('data-msg') || DEFAULT_WELCOME_MSG;
            userInput.value = idea;
            adjustInputState();
            sendMessage();
        });
    }


    updateFooterPosition();
    updateChatPadding();
    window.addEventListener('resize', () => {
        updateFooterPosition();
        updateChatPadding();
    });
    // Keep input centered within the app container
    updateInputPosition();
    window.addEventListener('resize', updateInputPosition);
    // Update time-ago labels periodically
    setInterval(updateAllTimeAgo, 60000);


    // Function to extract image paths from content
    function extractImagePaths(content) {
        if (typeof content !== 'string') return { text: content, images: [] };
    
        // Match image paths, external image URLs, and LOGO IDs
        const imageRegex = new RegExp(
            '(https?:\\/\\/[\\w.-]+(?:\\/[\\w\\-._~:\\/?#\\[\\]@!$&\'()*+,;=]*)?\\.(?:png|jpg|jpeg|svg|webp)(?:\\?[^\\s\"\\\'<>]*)?|\\/outputs\\/[^\\s\"\\\'()<>]+\\.(?:png|jpg|jpeg|svg|webp)|LOGO_[a-f0-9]+)',
            'gi'
        );
        const images = [];
        let match;
    
        // Find all matches
        while ((match = imageRegex.exec(content)) !== null) {
            images.push(match[0]);
        }
    
        console.log("Found images:", images);
    
        // Remove image paths from the text while preserving line breaks
        let text = content;
        images.forEach(img => {
            text = text.replace(img, '');
        });
    
        // Normalize whitespace but PRESERVE newlines for markdown parsing
        text = text
            .replace(/\r\n/g, '\n')           // normalize CRLF
            .replace(/[\t ]+/g, ' ')            // collapse tabs/spaces
            .replace(/[\t ]*\n[\t ]*/g, '\n') // trim around newlines
            .trim();
    
        return { text, images };
    }

    // Markdown-aware, HTML-safe rendering for better chatbot formatting
    function renderContentToHtml(text) {
        if (typeof text !== 'string' || !text) return '';
        // Escape HTML
        let escaped = text
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');

        // Preserve code blocks ```code```
        let html = escaped.replace(/```([\s\S]*?)```/g, (m, code) => {
            const restored = code.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>');
            return `<pre><code>${restored}</code></pre>`;
        });

        // Inline code `code`
        html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

        // Process lines for lists, headings, and tables
        const lines = html.split(/\n/g);
        let out = '';
        let inUl = false;
        let inOl = false;
        let tableBuffer = [];
        const closeLists = () => {
            if (inUl) { out += '</ul>'; inUl = false; }
            if (inOl) { out += '</ol>'; inOl = false; }
        };
        const isMdTableRow = (line) => /^\|.*\|$/.test(line);
        const isAlignRow = (cells) => cells.every(c => /^:?-{2,}:?$/.test(c));
        const flushTable = () => {
            if (tableBuffer.length === 0) return;
            const rows = tableBuffer.map(l => l.slice(1, -1).split('|').map(c => c.trim()));
            let header = null;
            let body = rows;
            if (rows.length >= 2 && isAlignRow(rows[1])) {
                header = rows[0];
                body = rows.slice(2);
            }
            out += '<table class="md-table">';
            if (header) {
                out += '<thead><tr>' + header.map(h => `<th>${h}</th>`).join('') + '</tr></thead>';
            }
            out += '<tbody>' + body.map(r => '<tr>' + r.map(c => `<td>${c}</td>`).join('') + '</tr>').join('') + '</tbody></table>';
            tableBuffer = [];
        };
        let inPre = false;
        for (let raw of lines) {
            const line = raw.trim();
            if (!line) { continue; }
            if (line.includes('<pre><code')) { inPre = true; out += line; continue; }
            if (inPre) { out += line; if (line.includes('</code></pre>')) inPre = false; continue; }
            // Markdown tables
            if (isMdTableRow(line)) { tableBuffer.push(line); continue; }
            if (tableBuffer.length) { flushTable(); }
            // Unordered list items
            if (/^(?:[-*•])\s+/.test(line)) {
                if (!inUl) { closeLists(); out += '<ul>'; inUl = true; }
                const item = line.replace(/^(?:[-*])\s+/, '');
                const itemFmt = item.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>').replace(/\*([^*]+)\*/g, '<em>$1</em>');
                out += `<li>${itemFmt}</li>`;
                continue;
            }
            // Ordered list items
            if (/^\d+[.)]\s+/.test(line)) {
                if (!inOl) { closeLists(); out += '<ol>'; inOl = true; }
                const item = line.replace(/^\d+[.)]\s+/, '');
                const itemFmt = item.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>').replace(/\*([^*]+)\*/g, '<em>$1</em>');
                out += `<li>${itemFmt}</li>`;
                continue;
            }
            // Headings
            const hMatch = line.match(/^(#{1,6})\s+(.*)$/);
            if (hMatch) {
                closeLists();
                const level = hMatch[1].length;
                const text = hMatch[2];
                const fmt = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>').replace(/\*([^*]+)\*/g, '<em>$1</em>');
                out += `<h${level}>${fmt}</h${level}>`;
                continue;
            }
            // Regular paragraph
            closeLists();
            const para = line.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>').replace(/\*([^*]+)\*/g, '<em>$1</em>');
            out += `<p>${para}</p>`;
        }
        if (tableBuffer.length) { flushTable(); }
        closeLists();

        // Color palette fallback: convert name + HEX lines into a table with swatches
        try {
            const rawLines = text.split(/\n/g);
            const paletteRows = [];
            for (const l of rawLines) {
                const m = l.match(/^\s*(?:[-*]\s*)?(.*?)\s*(?:[:\-–])\s*(#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}))/);
                const m2 = m || l.match(/\b(#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}))\b/);
                if (m2) {
                    const name = (m && m[1] ? m[1].trim() : 'Color');
                    const hex = (m ? m[2] : m2[1]).toUpperCase();
                    paletteRows.push({ name, hex });
                }
            }
            const uniqueRows = [];
            const seen = new Set();
            for (const r of paletteRows) {
                const key = r.name + '|' + r.hex;
                if (!seen.has(key)) { seen.add(key); uniqueRows.push(r); }
            }
            if (uniqueRows.length >= 2 && !out.includes('<table')) {
                out += '<table class="md-table"><thead><tr><th>Color</th><th>HEX</th><th>Preview</th></tr></thead><tbody>' +
                    uniqueRows.map(r => `<tr><td>${r.name}</td><td>${r.hex}</td><td><span class="swatch" style="background:${r.hex}"></span></td></tr>`).join('') +
                    '</tbody></table>';
            }
        } catch (e) { /* no-op */ }

        return out;
    }

    // Build reasoning summary by extracting steps/bullets
    function buildReasoningSummary(text) {
        if (!text || typeof text !== 'string') return [];
        const lines = text.split(/\n|<br>/g).map(l => l.trim()).filter(Boolean);
        const steps = [];
        for (const l of lines) {
            if (/^(?:[-*•]|\d+[.)]|step\s*\d+[:.])/i.test(l)) {
                steps.push(l.replace(/^[-*•]\s*/, ''));
            }
        }
        // Fallback: take first 3 sentences as outline
        if (steps.length === 0) {
            const sentences = text.split(/(?<=[.!?])\s+/).slice(0, 3);
            sentences.forEach(s => {
                if (s && s.trim()) steps.push(s.trim());
            });
        }
        return steps.slice(0, 6);
    }

    // Formatting helpers
    function formatElapsed(ms) {
        if (ms == null) return '';
        if (ms < 1000) return `${ms} ms`;
        const s = (ms / 1000);
        if (s < 60) return `${s.toFixed(1)} s`;
        const m = Math.floor(s / 60);
        const rem = s % 60;
        return `${m}m ${rem.toFixed(0)}s`;
    }

    function formatElapsedNumeric(ms) {
        if (ms == null) return '';
        if (ms < 1000) return `${ms}`;
        const s = (ms / 1000);
        return s.toFixed(1);
    }

    // Relative time helper, e.g., "4 minutes ago"
    function formatTimeAgo(ts) {
        const now = Date.now();
        const diff = Math.max(0, now - ts);
        const sec = Math.floor(diff / 1000);
        if (sec < 5) return 'just now';
        if (sec < 60) return `${sec} seconds ago`;
        const min = Math.floor(sec / 60);
        if (min < 60) return `${min} minute${min === 1 ? '' : 's'} ago`;
        const hrs = Math.floor(min / 60);
        if (hrs < 24) return `${hrs} hour${hrs === 1 ? '' : 's'} ago`;
        const days = Math.floor(hrs / 24);
        return `${days} day${days === 1 ? '' : 's'} ago`;
    }

    function updateAllTimeAgo() {
        const nodes = document.querySelectorAll('.time-ago[data-timestamp]');
        nodes.forEach(n => {
            const ts = parseInt(n.getAttribute('data-timestamp'), 10);
            if (!isNaN(ts)) n.textContent = formatTimeAgo(ts);
        });
    }

    // Smooth typing animation with blinking caret
    function typeTextWithCaret(targetEl, rawText, opts = {}) {
        const speed = Math.max(8, Math.min(100, opts.speedMs || 16));
        const caret = document.createElement('span');
        caret.className = 'typing-caret';
        targetEl.appendChild(caret);

        let i = 0;
        function step() {
            if (i >= rawText.length) {
                caret.remove();
                if (typeof opts.onComplete === 'function') opts.onComplete();
                return;
            }
            const ch = rawText[i++];
            if (ch === '\n') {
                targetEl.insertBefore(document.createElement('br'), caret);
            } else {
                targetEl.insertBefore(document.createTextNode(ch), caret);
            }
            let delay = speed;
            if (/[.,!?]/.test(ch)) delay = speed * 3; // shorter pause on punctuation
            setTimeout(step, delay);
        }
        step();
    }

    // Per-word typing animation for assistant messages
    function typeTextByWord(targetEl, rawText, opts = {}) {
        const cssSpeed = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--typing-word-speed-ms').trim(), 10);
        const speed = Math.max(40, Math.min(800, opts.speedMs || (isNaN(cssSpeed) ? 120 : cssSpeed)));
        targetEl.innerHTML = '';
        const tokens = String(rawText).split(/(\s+)/); // keep whitespace tokens
        let t = 0;
        tokens.forEach(tok => {
            if (/^\s+$/.test(tok)) {
                targetEl.appendChild(document.createTextNode(tok));
            } else {
                const span = document.createElement('span');
                span.className = 'thinking-word'; // reuse smooth fade-in styles
                span.textContent = tok;
                targetEl.appendChild(span);
                setTimeout(() => span.classList.add('in'), t);
                t += /[.,!?]$/.test(tok) ? speed * 1.5 : speed; // slight pause on punctuation
            }
        });
        setTimeout(() => { if (typeof opts.onComplete === 'function') opts.onComplete(); }, t + 10);
    }

    // Per-word thinking animation for the loading indicator
    function playThinkingWords(targetEl, rawText, opts = {}) {
        const cssSpeed = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--thinking-speed-ms').trim(), 10);
        const speed = Math.max(20, Math.min(400, opts.speedMs || (isNaN(cssSpeed) ? 60 : cssSpeed)));
        targetEl.innerHTML = '';
        // Replace dynamic placeholder with a random status
        const statuses = ['thinking', 'generating', 'creating'];
        let text = String(rawText);
        if (text.includes('${condeyisthinking}')) {
            const pick = statuses[Math.floor(Math.random() * statuses.length)];
            text = text.replace('${condeyisthinking}', pick);
        }
        const words = String(text).split(/\s+/).filter(Boolean);
        words.forEach((w, idx) => {
            const span = document.createElement('span');
            span.className = 'thinking-word';
            span.textContent = w;
            targetEl.appendChild(span);
            if (idx < words.length - 1) targetEl.appendChild(document.createTextNode(' '));
            setTimeout(() => span.classList.add('in'), idx * speed);
        });
    }

    // Start dynamic thinking status rotation for the loading indicator
    function startThinkingStatusRotation(targetEl) {
        const speedMs = (() => {
            const cssSpeed = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--thinking-speed-ms').trim(), 10);
            return isNaN(cssSpeed) ? 60 : cssSpeed;
        })();
        // Initial render
        playThinkingWords(targetEl, 'Codey is ${condeyisthinking}...', { speedMs });
        const id = setInterval(() => {
            playThinkingWords(targetEl, 'Codey is ${condeyisthinking}...', { speedMs });
        }, Math.max(900, speedMs * 10));
        return id;
    }

    // Build message row; remove user avatar while preserving assistant avatar
    function buildMessageRow(role, inner) {
        const row = document.createElement('div');
        row.className = 'message-row ' + role;

        if (role === 'assistant') {
            const avatar = document.createElement('img');
            avatar.className = 'avatar assistant-avatar';
            avatar.alt = 'Codey Avatar';
            avatar.src = (personaAvatarImg && personaAvatarImg.src) ? personaAvatarImg.src : 'assets/female-2.webp';
            row.appendChild(avatar);
            row.appendChild(inner);
        } else {
            // For user messages, omit avatar for a cleaner look
            row.appendChild(inner);
        }
        return row;
    }

    // Add message to chat
    function addMessageToChat(role, content, options = {}) {
        // Row wrapper for consistent separators and padding
        const containerDiv = document.createElement('div');
        containerDiv.className = 'message-container';

        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${role}-message`;
        
        if (role === 'assistant') {
            // For assistant messages, check for image paths
            const { text, images } = extractImagePaths(content);
    
            // Helper to convert an absolute file path to a public /outputs URL
            const toPublicOutputsPath = (p) => {
                try {
                    if (!p || typeof p !== 'string') return null;
                    const norm = p.replace(/\\/g, '/');
                    const idx = norm.toLowerCase().indexOf('/outputs/');
                    if (idx !== -1) return norm.slice(idx);
                    // Some containers may prefix with /app
                    const appIdx = norm.toLowerCase().indexOf('/app/outputs/');
                    if (appIdx !== -1) return norm.slice(appIdx + '/app'.length);
                    return null;
                } catch (e) { return null; }
            };
    
            // Helper: append images (logos render after typing completes)
            const appendImages = () => {
                if (images && images.length > 0) {
                    images.forEach(imgPath => {
                        const imgContainer = document.createElement('div');
                        imgContainer.className = 'image-container';

                        const isCodeyAzielFrom = (str) => /Codey[_\s-]?Aziel/i.test(String(str || ''));
                        const appendActionsAndExplanation = (publicPath, meta) => {
                            const actions = document.createElement('div');
                            actions.className = 'image-actions';
                            const dl = document.createElement('a');
                            dl.href = publicPath;
                            dl.download = '';
                            dl.className = 'download-link';
                            dl.textContent = 'Download';
                            actions.appendChild(dl);
                            imgContainer.appendChild(actions);
                            try {
                                const promptText = meta && meta.prompt ? String(meta.prompt) : '';
                                const brandHit = isCodeyAzielFrom(promptText) || isCodeyAzielFrom(publicPath);
                                const pathLower = String(publicPath || '').toLowerCase();
                                const isLogoPath = /\/outputs\/logos\//i.test(pathLower);
                                if (brandHit || isLogoPath) {
                                    const expl = document.createElement('div');
                                    expl.className = 'logo-explanation';
                                    const p = document.createElement('p');
                                    p.textContent = 'I chose this logo for its clean, modern feel and balanced composition. The color choice aligns with the brand\'s personality and helps it stand out confidently.';
                                    expl.appendChild(p);
                                    imgContainer.appendChild(expl);
                                }
                            } catch (e) { /* no-op */ }
                        };

                        if (imgPath.startsWith('LOGO_')) {
                            const logoId = imgPath.split('_')[1];

                            const appendImg = (path, meta=null) => {
                                const webPath = toPublicOutputsPath(path) || path;
                                const img = document.createElement('img');
                                img.src = webPath;
                                img.alt = 'Generated logo';
                                img.className = 'response-image';
                                img.loading = 'lazy';
                                img.decoding = 'async';
                                img.onerror = () => {
                                    img.style.display = 'none';
                                    const errorText = document.createElement('p');
                                    errorText.textContent = `[Image could not be loaded: ${webPath}]`;
                                    errorText.style.color = '#e53e3e';
                                    errorText.style.fontSize = '0.8rem';
                                    imgContainer.appendChild(errorText);
                                };
                                imgContainer.appendChild(img);
                                appendActionsAndExplanation(webPath, meta);
                                messageDiv.appendChild(imgContainer);
                            };

                            fetch(`/generated_images/LOGO_${logoId}_latest.json`)
                                .then(res => res.ok ? res.json() : Promise.reject(new Error('latest not found')))
                                .then(meta => {
                                    const rawPath = meta && meta.image_path ? String(meta.image_path) : '';
                                    if (rawPath) {
                                        appendImg(rawPath, meta);
                                    } else {
                                        throw new Error('empty latest path');
                                    }
                                })
                                .catch(() => {
                                    return fetch(`/generated_images/LOGO_${logoId}_history.json`)
                                        .then(res => res.ok ? res.json() : Promise.reject(new Error('history not found')))
                                        .then(hist => {
                                            const last = Array.isArray(hist) && hist.length ? hist[hist.length - 1] : null;
                                            const rawPath = last && last.image_path ? String(last.image_path) : '';
                                            if (rawPath) {
                                                appendImg(rawPath, last);
                                            } else {
                                                throw new Error('no history path');
                                            }
                                        });
                                })
                                .catch(() => {
                                    const fallbackText = document.createElement('p');
                                    fallbackText.textContent = `[Logo ID: ${logoId}]`;
                                    fallbackText.style.color = '#718096';
                                    fallbackText.style.fontSize = '0.8rem';
                                    imgContainer.appendChild(fallbackText);
                                    messageDiv.appendChild(imgContainer);
                                });
                        } else {
                            const webPath = toPublicOutputsPath(imgPath) || imgPath;
                            const img = document.createElement('img');
                            img.src = webPath;
                            img.alt = 'Generated image';
                            img.className = 'response-image';
                            img.loading = 'lazy';
                            img.decoding = 'async';
                            img.onerror = () => {
                                img.style.display = 'none';
                                const errorText = document.createElement('p');
                                errorText.textContent = `[Image could not be loaded: ${webPath}]`;
                                errorText.style.color = '#e53e3e';
                                errorText.style.fontSize = '0.8rem';
                                imgContainer.appendChild(errorText);
                            };
                            imgContainer.appendChild(img);
                            appendActionsAndExplanation(webPath, null);
                            messageDiv.appendChild(imgContainer);
                        }
                    });
                }
            };
    
            // Helper: meta footer (timestamp + copy icon)
            const insertAssistantMeta = () => {
                const meta = document.createElement('div');
                meta.className = 'message-meta';
                if (options.elapsedMs != null) {
                    const timeEl = document.createElement('span');
                    timeEl.className = 'MuiTypography-root MuiTypography-caption shining-left css-z5ufvz';
                    timeEl.textContent = `Generated in ${formatElapsed(options.elapsedMs)}`;
                    meta.appendChild(timeEl);
                }
                const createdAt = Date.now();
                const agoEl = document.createElement('span');
                agoEl.className = 'time-ago';
                agoEl.setAttribute('data-timestamp', String(createdAt));
                agoEl.textContent = formatTimeAgo(createdAt);
                meta.appendChild(agoEl);
                const regenBtn = document.createElement('button');
                regenBtn.className = 'regen-btn';
                regenBtn.title = 'Regenerate';
                regenBtn.setAttribute('aria-label', 'Regenerate response');
                regenBtn.innerHTML = '<svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor" xmlns="http://www.w3.org/2000/svg" class="icon"><path d="M3.502 16.6663V13.3333C3.502 12.9661 3.79977 12.6683 4.16704 12.6683H7.50004L7.63383 12.682C7.93691 12.7439 8.16508 13.0119 8.16508 13.3333C8.16508 13.6547 7.93691 13.9227 7.63383 13.9847L7.50004 13.9984H5.47465C6.58682 15.2249 8.21842 16.0013 10 16.0013C13.06 16.0012 15.5859 13.711 15.9551 10.7513L15.9854 10.6195C16.0845 10.3266 16.3785 10.1334 16.6973 10.1732C17.0617 10.2186 17.3198 10.551 17.2745 10.9154L17.2247 11.2523C16.6301 14.7051 13.6224 17.3313 10 17.3314C8.01103 17.3314 6.17188 16.5383 4.83208 15.2474V16.6663C4.83208 17.0335 4.53411 17.3311 4.16704 17.3314C3.79977 17.3314 3.502 17.0336 3.502 16.6663ZM4.04497 9.24935C3.99936 9.61353 3.66701 9.87178 3.30278 9.8265C2.93833 9.78105 2.67921 9.44876 2.72465 9.08431L4.04497 9.24935ZM10 2.66829C11.9939 2.66833 13.8372 3.46551 15.1778 4.76204V3.33333C15.1778 2.96616 15.4757 2.66844 15.8428 2.66829C16.2101 2.66829 16.5079 2.96606 16.5079 3.33333V6.66634C16.5079 7.03361 16.2101 7.33138 15.8428 7.33138H12.5098C12.1425 7.33138 11.8448 7.03361 11.8448 6.66634C11.8449 6.29922 12.1426 6.0013 12.5098 6.0013H14.5254C13.4133 4.77488 11.7816 3.99841 10 3.99837C6.93998 3.99837 4.41406 6.28947 4.04497 9.24935L3.38481 9.16634L2.72465 9.08431C3.17574 5.46702 6.26076 2.66829 10 2.66829Z"></path></svg>';
                regenBtn.addEventListener('click', () => {
                    try {
                        if (isGenerating) return;
                        // Use the last user message for regeneration; configurable to target specific threads if desired
                        const text = (lastUserMessage || '').trim();
                        if (!text) return;
                        userInput.value = text;
                        sendMessage();
                    } catch (e) { /* no-op */ }
                });
                meta.appendChild(regenBtn);

                const copyBtn = document.createElement('button');
                copyBtn.className = 'copy-btn';
                copyBtn.title = 'Copy';
                copyBtn.setAttribute('aria-label', 'Copy message');
                copyBtn.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" aria-hidden="true" role="img" class="component-iconify MuiBox-root css-1570kgy iconify iconify--solar" width="1em" height="1em" viewBox="0 0 24 24"><path fill="currentColor" d="M6.6 11.397c0-2.726 0-4.089.843-4.936c.844-.847 2.201-.847 4.917-.847h2.88c2.715 0 4.073 0 4.916.847c.844.847.844 2.21.844 4.936v4.82c0 2.726 0 4.089-.844 4.936c-.843.847-2.201.847-4.916.847h-2.88c-2.716 0-4.073 0-4.917-.847s-.843-2.21-.843-4.936z"></path><path fill="currentColor" d="M4.172 3.172C3 4.343 3 6.229 3 10v2c0 3.771 0 5.657 1.172 6.828c.617.618 1.433.91 2.62 1.048c-.192-.84-.192-1.996-.192-3.66v-4.819c0-2.726 0-4.089.843-4.936c.844-.847 2.201-.847 4.917-.847h2.88c1.652 0 2.8 0 3.638.19c-.138-1.193-.43-2.012-1.05-2.632C16.657 2 14.771 2 11 2S5.343 2 4.172 3.172" opacity=".5"></path></svg>';
                copyBtn.addEventListener('click', async () => {
                    try {
                        const textToCopy = messageDiv.innerText.trim();
                        await navigator.clipboard.writeText(textToCopy);
                        copyBtn.classList.add('copied');
                        setTimeout(() => copyBtn.classList.remove('copied'), 1200);
                    } catch (e) {
                        console.error('Copy failed', e);
                    }
                });
                meta.appendChild(copyBtn);
                messageDiv.appendChild(meta);
            };

    
            // Render text instantly, no typing animations (HTML formatting + meta)
            if (text && text.trim() !== '') {
                const html = renderContentToHtml(text);
                messageDiv.insertAdjacentHTML('beforeend', html);
                appendImages();
                insertAssistantMeta();
                updateAllTimeAgo();
            } else {
                // No text; show images and meta immediately
                appendImages();
                insertAssistantMeta();
                updateAllTimeAgo();
            }
        } else {
            // For user messages: render formatted HTML + meta
            const html = renderContentToHtml(content);
            messageDiv.insertAdjacentHTML('beforeend', html);
            if (options.images && options.images.length) {
                options.images.forEach(src => {
                    const imgContainer = document.createElement('div');
                    imgContainer.className = 'image-container';
                    const img = document.createElement('img');
                    img.src = src;
                    img.alt = 'Attachment';
                    img.className = 'response-image';
                    imgContainer.appendChild(img);
                    const expl = document.createElement('div');
                    expl.className = 'logo-explanation';
                    expl.innerHTML = `
                        <strong>Image Explanation</strong>
                        <ul>
                            <li>Attachment from your device</li>
                            <li>Displayed inline for quick review</li>
                        </ul>
                    `;
                    imgContainer.appendChild(expl);
                    messageDiv.appendChild(imgContainer);
                });
            }

            // Meta footer: time ago + copy icon for user messages
            {
                const meta = document.createElement('div');
                meta.className = 'message-meta';
                const createdAt = Date.now();
                const agoEl = document.createElement('span');
                agoEl.className = 'time-ago';
                agoEl.setAttribute('data-timestamp', String(createdAt));
                agoEl.textContent = formatTimeAgo(createdAt);
                meta.appendChild(agoEl);
                const copyBtn = document.createElement('button');
                copyBtn.className = 'copy-btn';
                copyBtn.title = 'Copy';
                copyBtn.setAttribute('aria-label', 'Copy message');
                copyBtn.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" aria-hidden="true" role="img" class="component-iconify MuiBox-root css-1570kgy iconify iconify--solar" width="1em" height="1em" viewBox="0 0 24 24"><path fill="currentColor" d="M6.6 11.397c0-2.726 0-4.089.843-4.936c.844-.847 2.201-.847 4.917-.847h2.88c2.715 0 4.073 0 4.916.847c.844.847.844 2.21.844 4.936v4.82c0 2.726 0 4.089-.844 4.936c-.843.847-2.201.847-4.916.847h-2.88c-2.716 0-4.073 0-4.917-.847s-.843-2.21-.843-4.936z"></path><path fill="currentColor" d="M4.172 3.172C3 4.343 3 6.229 3 10v2c0 3.771 0 5.657 1.172 6.828c.617.618 1.433.91 2.62 1.048c-.192-.84-.192-1.996-.192-3.66v-4.819c0-2.726 0-4.089.843-4.936c.844-.847 2.201-.847 4.917-.847h2.88c1.652 0 2.8 0 3.638.19c-.138-1.193-.43-2.012-1.05-2.632C16.657 2 14.771 2 11 2S5.343 2 4.172 3.172" opacity=".5"></path></svg>';
                copyBtn.addEventListener('click', async () => {
                    try {
                        const textToCopy = messageDiv.innerText.trim();
                        await navigator.clipboard.writeText(textToCopy);
                        copyBtn.classList.add('copied');
                        setTimeout(() => copyBtn.classList.remove('copied'), 1200);
                    } catch (e) {
                        console.error('Copy failed', e);
                    }
                });
                meta.appendChild(copyBtn);
                messageDiv.appendChild(meta);
            }

        }
        
        const row = buildMessageRow(role, messageDiv);
        containerDiv.appendChild(row);
        chatMessages.appendChild(containerDiv);
        chatMessages.scrollTop = chatMessages.scrollHeight;
        containerDiv.scrollIntoView({ behavior: 'smooth', block: 'end' });
        
        // Update chat history (store created time for completeness)
        if (!options.skipHistory) {
            chatHistory.push({ role, content, elapsedMs: options.elapsedMs || null, createdAt: Date.now() });
            const conv = getActiveConv();
            if (conv) {
                conv.history = chatHistory.slice();
                saveConversations();
                renderConversationList();
            }
        }
        // Refresh time-ago labels immediately
        updateAllTimeAgo();
    }
    // Build CopilotKit-style messages payload from chatHistory
    function buildCopilotMessages() {
        try {
            return (chatHistory || []).map(m => ({ role: m.role, content: m.content }));
        } catch (e) {
            return [];
        }
    }

    // Progress card state and helpers
    let progressCard = null;
    let progressBar = null;
    let progressCount = null;
    let progressStepsWrap = null;
    const stepMap = new Map();

    function ensureProgressCard(title, total) {
        if (!progressCard) {
            progressCard = document.createElement('div');
            progressCard.className = 'progress-card';
            progressCard.innerHTML = `
                <div class="progress-header">
                    <div class="title">${title || 'Task Progress'}</div>
                    <div class="count"><span class="pcount">0/${total} Complete</span></div>
                </div>
                <div class="progress-bar-wrap"><div class="progress-bar"></div></div>
                <div class="progress-steps"></div>
            `;
            chatMessages.appendChild(progressCard);
            chatMessages.scrollTop = chatMessages.scrollHeight;
            progressBar = progressCard.querySelector('.progress-bar');
            progressCount = progressCard.querySelector('.pcount');
            progressStepsWrap = progressCard.querySelector('.progress-steps');
        } else {
            // update header
            const ttlEl = progressCard.querySelector('.progress-header .title');
            ttlEl.textContent = title || 'Task Progress';
            progressCount.textContent = `0/${total} Complete`;
            progressBar.style.width = '0%';
            stepMap.clear();
            progressStepsWrap.innerHTML = '';
        }
        progressCard.dataset.total = String(total || 0);
    }

    function upsertStep(id, title, status) {
        let row = stepMap.get(id);
        if (!row) {
            row = document.createElement('div');
            row.className = 'progress-step';
            row.innerHTML = `<div class="icon"></div><div class="txt"></div>`;
            stepMap.set(id, row);
            progressStepsWrap.appendChild(row);
        }
        row.classList.remove('running', 'complete');
        if (status === 'running') row.classList.add('running');
        if (status === 'complete') row.classList.add('complete');
        const txt = row.querySelector('.txt');
        txt.textContent = title || id;

        // update count + bar on complete
        if (status === 'complete') {
            const total = Number(progressCard?.dataset?.total || 0);
            const completed = [...stepMap.values()].filter(r => r.classList.contains('complete')).length;
            progressCount.textContent = `${completed}/${total} Complete`;
            const pct = total ? Math.round((completed/total)*100) : 0;
            progressBar.style.width = pct + '%';
        }
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    function finalizeProgress(completed, total) {
        if (!progressCard) return;
        const pct = total ? Math.round((completed/total)*100) : 100;
        progressBar.style.width = pct + '%';
        progressCount.textContent = `${completed}/${total} Complete`;
    }

    // Stream CopilotKit chat via SSE-like response (POST + text/event-stream)
    async function streamCopilotChat(messages, onChunk, onProgress, opts = {}) {
        try {
            const response = await fetch(`${API_BASE}/api/copilot/chat/stream`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'text/event-stream'
                },
                body: JSON.stringify({ messages }),
                signal: opts.signal
            });
            if (!response.ok) throw new Error(`Stream failed: ${response.status}`);
            const reader = response.body.getReader();
            const decoder = new TextDecoder('utf-8');
            let done = false;
            let buffer = '';
            let hadChunks = false;
            let sawDone = false;
            while (!done) {
                const { value, done: readerDone } = await reader.read();
                done = readerDone;
                if (value) {
                    buffer += decoder.decode(value, { stream: true });
                    const parts = buffer.split('\n\n');
                    // Keep last incomplete chunk in buffer
                    buffer = parts.pop() || '';
                    for (const part of parts) {
                        const lines = part.split('\n');
                        for (const line of lines) {
                            if (line.startsWith('data: ')) {
                                try {
                                    const evt = JSON.parse(line.slice(6));
                                    if (evt) {
                                        // Explicit server error event
                                        if (evt.type === 'error') {
                                            throw new Error(evt.message || 'Stream error');
                                        }
                                        // Progress events
                                        if (onProgress && evt.type) {
                                            onProgress(evt);
                                        }
                                        // Text chunks
                                        if (typeof evt.chunk === 'string') {
                                            hadChunks = true;
                                            if (evt.done) sawDone = true;
                                            onChunk && onChunk(evt.chunk, !!evt.done);
                                        }
                                    }
                                } catch (e) { /* ignore parse errors except explicit stream error */ }
                            }
                        }
                    }
                }
            }
            // If finished without an explicit done flag, finalize the UI
            if (hadChunks && !sawDone) {
                try { onChunk && onChunk('', true); } catch(_) {}
            }
            // If there were no chunks at all, still finalize the UI to remove loaders
            if (!hadChunks) {
                try { onChunk && onChunk('', true); } catch(_) {}
            }
        } catch (e) {
            throw e;
        }
    }

    async function sendMessage() {
        if (isGenerating) return;
        const message = userInput.value.trim();
        if (!message) return;
        // Auto-hide footer when a chat session begins
        hideCopilotFooter();
        // Mark generating and disable other interactions
        isGenerating = true;
        try { conversationListEl && conversationListEl.classList.add('generating'); } catch(_) {}
        try { inputContainer && inputContainer.classList.add('generating'); document.body && document.body.classList.add('generating'); } catch(_) {}
        // Capture original conversation to persist history correctly even if user switches
        const generatingConvId = activeConversationId;
        let generatingHistory = [];
        try {
            const conv0 = conversations.find(c => c.id === generatingConvId);
            generatingHistory = (conv0 && Array.isArray(conv0.history)) ? conv0.history.slice() : [];
        } catch (e) { generatingHistory = []; }
        // Ensure main chat UI is visible (in case user sends without selecting an agent)
        try { if (typeof revealApp === 'function') revealApp(); } catch(_) {}

        // Rename active conversation from default on first user message
        try {
            const conv = getActiveConv && getActiveConv();
            if (conv) {
                const isDefault = !conv.title || /^New Conversation$/i.test(conv.title) || /^Conversation$/i.test(conv.title);
                if (isDefault) {
                    const firstLine = (message.split('\n')[0] || '').trim();
                    const words = firstLine.split(/\s+/).filter(Boolean);
                    const title = words.slice(0, 4).join(' ');
                    if (title.length > 0) {
                        conv.title = title;
                        saveConversations();
                        renderConversationList();
                    }
                }
            }
        } catch (e) { /* no-op */ }

        lastUserMessage = message;
        // Add user message to chat
        addMessageToChat('user', message);
        // Also reflect this user message in the generating conversation's history in case user switches
        try {
            generatingHistory.push({ role: 'user', content: message, createdAt: Date.now() });
            persistConversationHistory(generatingConvId, generatingHistory);
        } catch (e) { /* no-op */ }
        // Interactive progress message handling removed (reverted)
        userInput.value = '';
        // Reset input state
        userInput.style.height = '';
        adjustInputState();

        // Show Stop and AI status while generating
        if (stopButton) {
            stopButton.style.display = 'inline-flex';
        }
        if (aiStatus) {
            aiStatus.style.display = 'inline-flex';
        }
        if (generatedNumberEl) {
            const badge = generatedNumberEl.querySelector('.badge');
            if (badge) badge.textContent = 'Generating...';
        }
        genStartTime = Date.now();

        // Prepare assistant message container for streaming
        let assistantMessageDiv = document.createElement('div');
        assistantMessageDiv.className = 'message assistant-message loading';
        assistantMessageDiv.textContent = 'Generating...';
        let assistantContainer = document.createElement('div');
        assistantContainer.className = 'message-container';
        const assistantRowInit = buildMessageRow('assistant', assistantMessageDiv);
        assistantContainer.appendChild(assistantRowInit);
        chatMessages.appendChild(assistantContainer);
        chatMessages.scrollTop = chatMessages.scrollHeight;
        let streamedContent = '';

        // Persist a pending assistant message so it shows up after refresh/switch
        let assistantPendingIndex = null;
        try {
            assistantPendingIndex = generatingHistory.length;
            generatingHistory.push({ role: 'assistant', content: 'Generating...', pending: true, createdAt: Date.now() });
            persistConversationHistory(generatingConvId, generatingHistory);
        } catch (e) { /* no-op */ }

        try {
            // Build messages for CopilotKit streaming
            const messages = buildCopilotMessages();
            messages.push({ role: 'user', content: message });

            currentAbortController = new AbortController();
            // Stream with progress events
            await streamCopilotChat(messages, 
                // onChunk callback
                (chunk, done) => {
                    // Update placeholder on first chunk
                    if (assistantMessageDiv.classList.contains('loading')) {
                        assistantMessageDiv.classList.remove('loading');
                    }
                    streamedContent += chunk;
                    assistantMessageDiv.textContent = streamedContent;
                    chatMessages.scrollTop = chatMessages.scrollHeight;
                    // Persist partial content so refresh shows current progress
                    try {
                        if (assistantPendingIndex != null) {
                            const entry = generatingHistory[assistantPendingIndex] || { role: 'assistant', content: '' };
                            entry.content = streamedContent;
                            entry.pending = !done;
                            generatingHistory[assistantPendingIndex] = entry;
                            persistConversationHistory(generatingConvId, generatingHistory);
                        }
                    } catch (e) { /* no-op */ }
                    
                    if (done) {
                        const elapsedMs = genStartTime ? (Date.now() - genStartTime) : null;
                        genStartTime = null;

                        // Render formatted HTML for the assistant message
                        assistantMessageDiv.classList.remove('loading');
                        const html = renderContentToHtml(streamedContent);
                        assistantMessageDiv.innerHTML = html;

                        // Append meta: time ago and copy button
                        const meta = document.createElement('div');
                        meta.className = 'message-meta';
                        const createdAt = Date.now();
                        const agoEl = document.createElement('span');
                        agoEl.className = 'time-ago';
                        agoEl.setAttribute('data-timestamp', String(createdAt));
                        agoEl.textContent = formatTimeAgo(createdAt);
                        meta.appendChild(agoEl);
                        const regenBtn = document.createElement('button');
                        regenBtn.className = 'regen-btn';
                        regenBtn.title = 'Regenerate';
                        regenBtn.setAttribute('aria-label', 'Regenerate response');
                        regenBtn.innerHTML = '<svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor" xmlns="http://www.w3.org/2000/svg" class="icon"><path d="M3.502 16.6663V13.3333C3.502 12.9661 3.79977 12.6683 4.16704 12.6683H7.50004L7.63383 12.682C7.93691 12.7439 8.16508 13.0119 8.16508 13.3333C8.16508 13.6547 7.93691 13.9227 7.63383 13.9847L7.50004 13.9984H5.47465C6.58682 15.2249 8.21842 16.0013 10 16.0013C13.06 16.0012 15.5859 13.711 15.9551 10.7513L15.9854 10.6195C16.0845 10.3266 16.3785 10.1334 16.6973 10.1732C17.0617 10.2186 17.3198 10.551 17.2745 10.9154L17.2247 11.2523C16.6301 14.7051 13.6224 17.3313 10 17.3314C8.01103 17.3314 6.17188 16.5383 4.83208 15.2474V16.6663C4.83208 17.0335 4.53411 17.3311 4.16704 17.3314C3.79977 17.3314 3.502 17.0336 3.502 16.6663ZM4.04497 9.24935C3.99936 9.61353 3.66701 9.87178 3.30278 9.8265C2.93833 9.78105 2.67921 9.44876 2.72465 9.08431L4.04497 9.24935ZM10 2.66829C11.9939 2.66833 13.8372 3.46551 15.1778 4.76204V3.33333C15.1778 2.96616 15.4757 2.66844 15.8428 2.66829C16.2101 2.66829 16.5079 2.96606 16.5079 3.33333V6.66634C16.5079 7.03361 16.2101 7.33138 15.8428 7.33138H12.5098C12.1425 7.33138 11.8448 7.03361 11.8448 6.66634C11.8449 6.29922 12.1426 6.0013 12.5098 6.0013H14.5254C13.4133 4.77488 11.7816 3.99841 10 3.99837C6.93998 3.99837 4.41406 6.28947 4.04497 9.24935L3.38481 9.16634L2.72465 9.08431C3.17574 5.46702 6.26076 2.66829 10 2.66829Z"></path></svg>';
                        regenBtn.addEventListener('click', () => {
                            try {
                                if (isGenerating) return;
                                const text = (lastUserMessage || '').trim();
                                if (!text) return;
                                userInput.value = text;
                                sendMessage();
                            } catch (e) { /* no-op */ }
                        });
                        meta.appendChild(regenBtn);
                        const copyBtn = document.createElement('button');
                        copyBtn.className = 'copy-btn';
                        copyBtn.title = 'Copy';
                        copyBtn.setAttribute('aria-label', 'Copy message');
                        copyBtn.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" aria-hidden="true" role="img" class="component-iconify MuiBox-root css-1570kgy iconify iconify--solar" width="1em" height="1em" viewBox="0 0 24 24"><path fill="currentColor" d="M6.6 11.397c0-2.726 0-4.089.843-4.936c.844-.847 2.201-.847 4.917-.847h2.88c2.715 0 4.073 0 4.916.847c.844.847.844 2.21.844 4.936v4.82c0 2.726 0 4.089-.844 4.936c-.843.847-2.201.847-4.916.847h-2.88c-2.716 0-4.073 0-4.917-.847s-.843-2.21-.843-4.936z"></path><path fill="currentColor" d="M4.172 3.172C3 4.343 3 6.229 3 10v2c0 3.771 0 5.657 1.172 6.828c.617.618 1.433.91 2.62 1.048c-.192-.84-.192-1.996-.192-3.66v-4.819c0-2.726 0-4.089.843-4.936c.844-.847 2.201-.847 4.917-.847h2.88c1.652 0 2.8 0 3.638.19c-.138-1.193-.43-2.012-1.05-2.632C16.657 2 14.771 2 11 2S5.343 2 4.172 3.172" opacity=".5"></path></svg>';
                        copyBtn.addEventListener('click', async () => {
                            try {
                                const textToCopy = assistantMessageDiv.innerText.trim();
                                await navigator.clipboard.writeText(textToCopy);
                                copyBtn.classList.add('copied');
                                setTimeout(() => copyBtn.classList.remove('copied'), 1200);
                            } catch (e) {
                                console.error('Copy failed', e);
                            }
                        });
                        meta.appendChild(copyBtn);
                        assistantMessageDiv.appendChild(meta);

                        // Finalize persisted assistant message and mark as complete
                         try {
                             if (assistantPendingIndex != null && generatingHistory[assistantPendingIndex]) {
                                 generatingHistory[assistantPendingIndex].content = streamedContent;
                                 delete generatingHistory[assistantPendingIndex].pending;
                                 persistConversationHistory(generatingConvId, generatingHistory);
                             }
                         } catch (e) { /* no-op */ }

                        // Update UI indicators
                        if (stopButton) { stopButton.style.display = 'none'; }
                        if (aiStatus) { aiStatus.style.display = 'none'; }

                        if (generatedNumberEl) {
                            const badge = generatedNumberEl.querySelector('.badge');
                            if (badge) {
                                badge.textContent = `Generated in ${elapsedMs ? formatElapsed(elapsedMs) : '—'}`;
                            }
                        }

                        // Re-enable conversation switching
                        isGenerating = false;
                        try { conversationListEl && conversationListEl.classList.remove('generating'); } catch(_) {}
                        try { inputContainer && inputContainer.classList.remove('generating'); document.body && document.body.classList.remove('generating'); } catch(_) {}

                        // Refresh time-ago labels immediately
                        updateAllTimeAgo();
                    }
                },
                // onProgress callback
                (evt) => {
                    if (evt.type === 'progress_init') {
                        ensureProgressCard(evt.title, evt.total);
                    } else if (evt.type === 'progress_step') {
                        upsertStep(evt.id, evt.title, evt.status);
                    } else if (evt.type === 'progress_complete') {
                        finalizeProgress(evt.completed, evt.total);
                    }
                },
                { signal: currentAbortController.signal }
            );

        } catch (error) {
            const elapsedMs = genStartTime ? (Date.now() - genStartTime) : null;
            genStartTime = null;
            let errorMsg = null;
            
            // Stop UI indicators
            if (stopButton) { stopButton.style.display = 'none'; }
            if (aiStatus) { aiStatus.style.display = 'none'; }
            
            const wasAborted = error && (error.name === 'AbortError' || /abort/i.test(error.message || ''));
            
            if (wasAborted) {
                if (assistantMessageDiv) {
                    assistantMessageDiv.classList.remove('loading');
                    const finalContent = streamedContent + '\n\n[Generation stopped]';
                    assistantMessageDiv.innerHTML = renderContentToHtml(finalContent);
                    // Append meta: time ago + copy button
                    const meta = document.createElement('div');
                    meta.className = 'message-meta';
                    const createdAt = Date.now();
                    const agoEl = document.createElement('span');
                    agoEl.className = 'time-ago';
                    agoEl.setAttribute('data-timestamp', String(createdAt));
                    agoEl.textContent = formatTimeAgo(createdAt);
                    meta.appendChild(agoEl);
                    const regenBtn = document.createElement('button');
                    regenBtn.className = 'regen-btn';
                    regenBtn.title = 'Regenerate';
                    regenBtn.setAttribute('aria-label', 'Regenerate response');
                    regenBtn.innerHTML = '<svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor" xmlns="http://www.w3.org/2000/svg" class="icon"><path d="M3.502 16.6663V13.3333C3.502 12.9661 3.79977 12.6683 4.16704 12.6683H7.50004L7.63383 12.682C7.93691 12.7439 8.16508 13.0119 8.16508 13.3333C8.16508 13.6547 7.93691 13.9227 7.63383 13.9847L7.50004 13.9984H5.47465C6.58682 15.2249 8.21842 16.0013 10 16.0013C13.06 16.0012 15.5859 13.711 15.9551 10.7513L15.9854 10.6195C16.0845 10.3266 16.3785 10.1334 16.6973 10.1732C17.0617 10.2186 17.3198 10.551 17.2745 10.9154L17.2247 11.2523C16.6301 14.7051 13.6224 17.3313 10 17.3314C8.01103 17.3314 6.17188 16.5383 4.83208 15.2474V16.6663C4.83208 17.0335 4.53411 17.3311 4.16704 17.3314C3.79977 17.3314 3.502 17.0336 3.502 16.6663ZM4.04497 9.24935C3.99936 9.61353 3.66701 9.87178 3.30278 9.8265C2.93833 9.78105 2.67921 9.44876 2.72465 9.08431L4.04497 9.24935ZM10 2.66829C11.9939 2.66833 13.8372 3.46551 15.1778 4.76204V3.33333C15.1778 2.96616 15.4757 2.66844 15.8428 2.66829C16.2101 2.66829 16.5079 2.96606 16.5079 3.33333V6.66634C16.5079 7.03361 16.2101 7.33138 15.8428 7.33138H12.5098C12.1425 7.33138 11.8448 7.03361 11.8448 6.66634C11.8449 6.29922 12.1426 6.0013 12.5098 6.0013H14.5254C13.4133 4.77488 11.7816 3.99841 10 3.99837C6.93998 3.99837 4.41406 6.28947 4.04497 9.24935L3.38481 9.16634L2.72465 9.08431C3.17574 5.46702 6.26076 2.66829 10 2.66829Z"></path></svg>';
                    regenBtn.addEventListener('click', () => {
                        try {
                            if (isGenerating) return;
                            const text = (lastUserMessage || '').trim();
                            if (!text) return;
                            userInput.value = text;
                            sendMessage();
                        } catch (e) { /* no-op */ }
                    });
                    meta.appendChild(regenBtn);
                    const copyBtn = document.createElement('button');
                    copyBtn.className = 'copy-btn';
                    copyBtn.title = 'Copy';
                    copyBtn.setAttribute('aria-label', 'Copy message');
                    copyBtn.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" aria-hidden="true" role="img" class="component-iconify MuiBox-root css-1570kgy iconify iconify--solar" width="1em" height="1em" viewBox="0 0 24 24"><path fill="currentColor" d="M6.6 11.397c0-2.726 0-4.089.843-4.936c.844-.847 2.201-.847 4.917-.847h2.88c2.715 0 4.073 0 4.916.847c.844.847.844 2.21.844 4.936v4.82c0 2.726 0 4.089-.844 4.936c-.843.847-2.201.847-4.916.847h-2.88c-2.716 0-4.073 0-4.917-.847s-.843-2.21-.843-4.936z"></path><path fill="currentColor" d="M4.172 3.172C3 4.343 3 6.229 3 10v2c0 3.771 0 5.657 1.172 6.828c.617.618 1.433.91 2.62 1.048c-.192-.84-.192-1.996-.192-3.66v-4.819c0-2.726 0-4.089.843-4.936c.844-.847 2.201-.847 4.917-.847h2.88c1.652 0 2.8 0 3.638.19c-.138-1.193-.43-2.012-1.05-2.632C16.657 2 14.771 2 11 2S5.343 2 4.172 3.172" opacity=".5"></path></svg>';
                    copyBtn.addEventListener('click', async () => {
                        try {
                            const textToCopy = assistantMessageDiv.innerText.trim();
                            await navigator.clipboard.writeText(textToCopy);
                            copyBtn.classList.add('copied');
                            setTimeout(() => copyBtn.classList.remove('copied'), 1200);
                        } catch (e) {
                            console.error('Copy failed', e);
                        }
                    });
                    meta.appendChild(copyBtn);
                    assistantMessageDiv.appendChild(meta);
                } else {
                    if (activeConversationId === generatingConvId) {
                        addMessageToChat('assistant', 'Generation stopped.', { elapsedMs });
                    }
                }
                if (generatedNumberEl) {
                    const badge = generatedNumberEl.querySelector('.badge');
                    if (badge) { badge.textContent = 'Generation stopped'; }
                }
            } else {
                errorMsg = 'Error: ' + (error && error.message ? error.message : 'Unknown error');
                if (assistantMessageDiv) {
                    assistantMessageDiv.classList.remove('loading');
                    const finalContent = streamedContent + '\n\n' + errorMsg;
                    assistantMessageDiv.innerHTML = renderContentToHtml(finalContent);
                    // Append meta: time ago + copy button
                    const meta = document.createElement('div');
                    meta.className = 'message-meta';
                    const createdAt = Date.now();
                    const agoEl = document.createElement('span');
                    agoEl.className = 'time-ago';
                    agoEl.setAttribute('data-timestamp', String(createdAt));
                    agoEl.textContent = formatTimeAgo(createdAt);
                    meta.appendChild(agoEl);
                    const regenBtn = document.createElement('button');
                    regenBtn.className = 'regen-btn';
                    regenBtn.title = 'Regenerate';
                    regenBtn.setAttribute('aria-label', 'Regenerate response');
                    regenBtn.innerHTML = '<svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor" xmlns="http://www.w3.org/2000/svg" class="icon"><path d="M3.502 16.6663V13.3333C3.502 12.9661 3.79977 12.6683 4.16704 12.6683H7.50004L7.63383 12.682C7.93691 12.7439 8.16508 13.0119 8.16508 13.3333C8.16508 13.6547 7.93691 13.9227 7.63383 13.9847L7.50004 13.9984H5.47465C6.58682 15.2249 8.21842 16.0013 10 16.0013C13.06 16.0012 15.5859 13.711 15.9551 10.7513L15.9854 10.6195C16.0845 10.3266 16.3785 10.1334 16.6973 10.1732C17.0617 10.2186 17.3198 10.551 17.2745 10.9154L17.2247 11.2523C16.6301 14.7051 13.6224 17.3313 10 17.3314C8.01103 17.3314 6.17188 16.5383 4.83208 15.2474V16.6663C4.83208 17.0335 4.53411 17.3311 4.16704 17.3314C3.79977 17.3314 3.502 17.0336 3.502 16.6663ZM4.04497 9.24935C3.99936 9.61353 3.66701 9.87178 3.30278 9.8265C2.93833 9.78105 2.67921 9.44876 2.72465 9.08431L4.04497 9.24935ZM10 2.66829C11.9939 2.66833 13.8372 3.46551 15.1778 4.76204V3.33333C15.1778 2.96616 15.4757 2.66844 15.8428 2.66829C16.2101 2.66829 16.5079 2.96606 16.5079 3.33333V6.66634C16.5079 7.03361 16.2101 7.33138 15.8428 7.33138H12.5098C12.1425 7.33138 11.8448 7.03361 11.8448 6.66634C11.8449 6.29922 12.1426 6.0013 12.5098 6.0013H14.5254C13.4133 4.77488 11.7816 3.99841 10 3.99837C6.93998 3.99837 4.41406 6.28947 4.04497 9.24935L3.38481 9.16634L2.72465 9.08431C3.17574 5.46702 6.26076 2.66829 10 2.66829Z"></path></svg>';
                    regenBtn.addEventListener('click', () => {
                        try {
                            if (isGenerating) return;
                            const text = (lastUserMessage || '').trim();
                            if (!text) return;
                            userInput.value = text;
                            sendMessage();
                        } catch (e) { /* no-op */ }
                    });
                    meta.appendChild(regenBtn);
                    const copyBtn = document.createElement('button');
                    copyBtn.className = 'copy-btn';
                    copyBtn.title = 'Copy';
                    copyBtn.setAttribute('aria-label', 'Copy message');
                    copyBtn.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" aria-hidden="true" role="img" class="component-iconify MuiBox-root css-1570kgy iconify iconify--solar" width="1em" height="1em" viewBox="0 0 24 24"><path fill="currentColor" d="M6.6 11.397c0-2.726 0-4.089.843-4.936c.844-.847 2.201-.847 4.917-.847h2.88c2.715 0 4.073 0 4.916.847c.844.847.844 2.21.844 4.936v4.82c0 2.726 0 4.089-.844 4.936c-.843.847-2.201.847-4.916.847h-2.88c-2.716 0-4.073 0-4.917-.847s-.843-2.21-.843-4.936z"></path><path fill="currentColor" d="M4.172 3.172C3 4.343 3 6.229 3 10v2c0 3.771 0 5.657 1.172 6.828c.617.618 1.433.91 2.62 1.048c-.192-.84-.192-1.996-.192-3.66v-4.819c0-2.726 0-4.089.843-4.936c.844-.847 2.201-.847 4.917-.847h2.88c1.652 0 2.8 0 3.638.19c-.138-1.193-.43-2.012-1.05-2.632C16.657 2 14.771 2 11 2S5.343 2 4.172 3.172" opacity=".5"></path></svg>';
                    copyBtn.addEventListener('click', async () => {
                        try {
                            const textToCopy = assistantMessageDiv.innerText.trim();
                            await navigator.clipboard.writeText(textToCopy);
                            copyBtn.classList.add('copied');
                            setTimeout(() => copyBtn.classList.remove('copied'), 1200);
                        } catch (e) {
                            console.error('Copy failed', e);
                        }
                    });
                    meta.appendChild(copyBtn);
                    assistantMessageDiv.appendChild(meta);
                } else {
                    if (activeConversationId === generatingConvId) {
                        addMessageToChat('assistant', errorMsg, { elapsedMs });
                    }
                }
                if (generatedNumberEl) {
                    const badge = generatedNumberEl.querySelector('.badge');
                    if (badge) { badge.textContent = `Generated in ${elapsedMs ? formatElapsed(elapsedMs) : '—'}`; }
                }
            }
            
            // Update pending assistant message with final error/aborted status in original conversation
            if (streamedContent) {
                const finalContent = wasAborted ? 
                    streamedContent + '\n\n[Generation stopped]' : 
                    streamedContent + '\n\n' + (errorMsg || '');
                try {
                    if (assistantPendingIndex != null && generatingHistory[assistantPendingIndex]) {
                        generatingHistory[assistantPendingIndex].content = finalContent;
                        delete generatingHistory[assistantPendingIndex].pending;
                        persistConversationHistory(generatingConvId, generatingHistory);
                    }
                } catch (e) { /* no-op */ }
            }

            // Re-enable conversation switching
            isGenerating = false;
            try { conversationListEl && conversationListEl.classList.remove('generating'); } catch(_) {}
            try { inputContainer && inputContainer.classList.remove('generating'); document.body && document.body.classList.remove('generating'); } catch(_) {}
            // Refresh time-ago labels immediately
            updateAllTimeAgo();
        }
    }

    // Event listeners
    sendButton.addEventListener('click', sendMessage);
    // Stop generation immediately on click
    if (stopButton) {
        stopButton.addEventListener('click', () => {
            try {
                if (currentAbortController) currentAbortController.abort();
            } catch(e) { /* no-op */ }
        });
    }

    // Footer toggle button
    const footerToggleBtn = document.getElementById('footer-toggle-btn');
    if (footerToggleBtn) {
        footerToggleBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            toggleCopilotFooter();
        });
    }
    
    userInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    // Suggestions chips (send directly on click, with footer hide)
    if (suggestions) {
        suggestions.addEventListener('click', (e) => {
            const t = e.target;
            // Carousel arrow controls
            if (t && t.classList && t.classList.contains('carousel-arrow')) {
                const carousel = suggestions.querySelector('.footer-carousel');
                if (carousel) {
                    const dir = t.classList.contains('prev') ? -1 : 1;
                    carousel.scrollBy({ left: dir * 240, behavior: 'smooth' });
                }
                return;
            }
            if (t && t.classList && t.classList.contains('suggestion-chip')) {
                // Hide footer on any item click
                hideCopilotFooter();
                // Clear chat action
                if (t.id === 'clear-chat') {
                    chatMessages.innerHTML = '';
                    chatHistory = [];
                    addMessageToChat('assistant', 'Chat cleared. How can I help you next?');
                    return;
                }
                const msg = t.getAttribute('data-msg');
                if (msg) {
                    // Populate input and send immediately
                    userInput.value = msg;
                    adjustInputState();
                    sendMessage();
                }
            }
        });
    }

    // Global click anywhere hides CopilotKit footer (except toggle button and footer itself)
    document.addEventListener('click', (ev) => {
        const target = ev.target;
        // Don't hide if clicking on toggle button or footer
        if (target && (
            target.closest('#footer-toggle-btn') || 
            target.closest('#copilotkit-footer')
        )) {
            return;
        }
        hideCopilotFooter();
    });


    // Hide footer on downward scroll of chat messages
    let lastScrollTop = 0;
    if (chatMessages) {
        chatMessages.addEventListener('scroll', () => {
            const st = chatMessages.scrollTop || 0;
            if (st > lastScrollTop + 8) {
                hideCopilotFooter();
            }
            lastScrollTop = st;
        });
    }

    // Attachment: trigger hidden file input and render previews
    if (attachButton && fileInput) {
        attachButton.addEventListener('click', () => fileInput.click());
        fileInput.addEventListener('change', async (e) => {
            const files = Array.from(e.target.files || []);
            if (!files.length) return;
            for (const file of files) {
                try {
                    if (file.type.startsWith('image/')) {
                        const reader = new FileReader();
                        reader.onload = () => {
                            addMessageToChat('user', `Attached ${file.name}`, { images: [reader.result] });
                        };
                        reader.readAsDataURL(file);
                    } else {
                        const info = `${file.name} (${Math.round(file.size/1024)} KB)`;
                        addMessageToChat('user', `Attached file: ${info}`);
                    }
                } catch (err) {
                    addMessageToChat('assistant', 'Attachment error: ' + err.message);
                }
            }
            // reset input so same file can be re-selected
            fileInput.value = '';
        });
    }

    // Voice input via Web Speech API
    if (voiceButton) {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (SpeechRecognition) {
            recognition = new SpeechRecognition();
            recognition.continuous = false;
            recognition.interimResults = false; // avoid interim duplicates
            recognition.maxAlternatives = 1;
            recognition.lang = 'en-US';
            recognition.onresult = (event) => {
                let transcript = '';
                for (let i = event.resultIndex; i < event.results.length; i++) {
                    const res = event.results[i];
                    if (res.isFinal && res[0] && res[0].transcript) {
                        transcript += res[0].transcript;
                    }
                }
                if (transcript) {
                    voiceAccumulated += (voiceAccumulated ? ' ' : '') + transcript.trim();
                    userInput.value = (baseInputBeforeVoice ? (baseInputBeforeVoice + ' ') : '') + voiceAccumulated;
                    adjustInputState();
                }
            };
            recognition.onend = () => {
                recognizing = false;
                voiceButton.classList.remove('recording');
                baseInputBeforeVoice = '';
                voiceAccumulated = '';
            };
            recognition.onerror = () => {
                recognizing = false;
                voiceButton.classList.remove('recording');
                baseInputBeforeVoice = '';
                voiceAccumulated = '';
            };
            voiceButton.addEventListener('click', () => {
                if (!recognizing) {
                    try {
                        baseInputBeforeVoice = (userInput.value || '').trim();
                        voiceAccumulated = '';
                        recognition.start();
                        recognizing = true;
                        voiceButton.classList.add('recording');
                    } catch(e) {
                        console.warn('Recognition start failed:', e);
                    }
                } else {
                    try { recognition.stop(); } catch(e) {}
                    recognizing = false;
                    voiceButton.classList.remove('recording');
                    baseInputBeforeVoice = '';
                    voiceAccumulated = '';
                }
            });
        } else {
            voiceButton.addEventListener('click', () => {
                addMessageToChat('assistant', 'Voice input is not supported in this browser.');
            });
        }
    }
});