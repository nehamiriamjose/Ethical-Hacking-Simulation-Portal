document.addEventListener("DOMContentLoaded", () => {
  const bell = document.getElementById("notifBell");
  const panel = document.getElementById("notifPanel");
  const list = document.getElementById("notifList");

  const params = new URLSearchParams(window.location.search);
  const email = params.get("email");

  bell.addEventListener("click", async (e) => {
    e.stopPropagation();
    panel.classList.toggle("show");

    if (!panel.classList.contains("show")) return;

    list.innerHTML = "Loading...";

    const res = await fetch(`/user/api/notifications?email=${email}`);
    const data = await res.json();

    if (data.length === 0) {
      list.innerHTML = "<div class='notif-item'>No notifications</div>";
      return;
    }

    list.innerHTML = "";

    data.forEach(n => {
      list.innerHTML += `
        <div class="notif-item" data-id="${n.id}">
          <button class="notif-dismiss" title="Dismiss">&times;</button>

          <div class="notif-item-title">${n.title}</div>
          <div class="notif-item-msg">${n.message}</div>
          <div class="notif-item-time">${n.created_at}</div>
        </div>
      `;
    });
  });

  // ✅ DISMISS HANDLER (MUST BE INSIDE DOMContentLoaded)
  list.addEventListener("click", (e) => {
    if (e.target.classList.contains("notif-dismiss")) {
      e.stopPropagation();
      e.target.closest(".notif-item").remove();
    }
  });

  // close panel when clicking outside
  document.addEventListener("click", () => {
    panel.classList.remove("show");
  });

  // ================= AI CHATBOT =================
  initChatbot();
});

// ================= CHATBOT INITIALIZATION =================
function initChatbot() {
  // Create chatbot HTML
  const chatbotHTML = `
    <button class="chatbot-toggle" id="chatbotToggle" title="AI Assistant">
      <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
        <!-- Robot head -->
        <rect x="4" y="6" width="16" height="14" rx="3" fill="currentColor"/>
        <!-- Antenna -->
        <line x1="12" y1="6" x2="12" y2="2" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        <circle cx="12" cy="2" r="1.5" fill="currentColor"/>
        <!-- Eyes -->
        <circle cx="8.5" cy="11" r="2" fill="#1a1a2e"/>
        <circle cx="15.5" cy="11" r="2" fill="#1a1a2e"/>
        <!-- Eye glow -->
        <circle cx="8.5" cy="11" r="1" fill="#00ff88"/>
        <circle cx="15.5" cy="11" r="1" fill="#00ff88"/>
        <!-- Mouth -->
        <rect x="8" y="15" width="8" height="2" rx="1" fill="#1a1a2e"/>
        <rect x="9" y="15.5" width="1.5" height="1" fill="#00ff88"/>
        <rect x="11.25" y="15.5" width="1.5" height="1" fill="#00ff88"/>
        <rect x="13.5" y="15.5" width="1.5" height="1" fill="#00ff88"/>
      </svg>
    </button>
    <div class="chatbot-container" id="chatbotContainer">
      <div class="chatbot-header">
        <div class="chatbot-header-icon">
          <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
            <rect x="4" y="6" width="16" height="14" rx="3" fill="currentColor"/>
            <line x1="12" y1="6" x2="12" y2="2" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            <circle cx="12" cy="2" r="1.5" fill="currentColor"/>
            <circle cx="8.5" cy="11" r="2" fill="#1a1a2e"/>
            <circle cx="15.5" cy="11" r="2" fill="#1a1a2e"/>
            <circle cx="8.5" cy="11" r="1" fill="#00ff88"/>
            <circle cx="15.5" cy="11" r="1" fill="#00ff88"/>
            <rect x="8" y="15" width="8" height="2" rx="1" fill="#1a1a2e"/>
          </svg>
        </div>
        <div class="chatbot-header-text">
          <h4>Hacker AI Assistant</h4>
          <span>Powered by Gemini</span>
        </div>
        <button class="chatbot-close" id="chatbotClose">&times;</button>
      </div>
      <div class="chatbot-messages" id="chatbotMessages">
        <div class="chatbot-message bot">
          👋 Hi! I'm your AI assistant for ethical hacking. Ask me anything about cybersecurity, CTF challenges, or penetration testing!
        </div>
      </div>
      <div class="chatbot-input-area">
        <input type="text" class="chatbot-input" id="chatbotInput" placeholder="Type your question..." />
        <button class="chatbot-send" id="chatbotSend">
          <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
            <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/>
          </svg>
        </button>
      </div>
    </div>
  `;

  // Append to body
  document.body.insertAdjacentHTML('beforeend', chatbotHTML);

  // Get elements
  const toggle = document.getElementById('chatbotToggle');
  const container = document.getElementById('chatbotContainer');
  const closeBtn = document.getElementById('chatbotClose');
  const input = document.getElementById('chatbotInput');
  const sendBtn = document.getElementById('chatbotSend');
  const messages = document.getElementById('chatbotMessages');

  // Toggle chatbot
  toggle.addEventListener('click', () => {
    container.classList.toggle('open');
    if (container.classList.contains('open')) {
      input.focus();
    }
  });

  // Close chatbot
  closeBtn.addEventListener('click', () => {
    container.classList.remove('open');
  });

  // Send message
  async function sendMessage() {
    const text = input.value.trim();
    if (!text) return;

    // Add user message
    addMessage(text, 'user');
    input.value = '';
    sendBtn.disabled = true;

    // Show typing indicator
    const typingId = addMessage('Thinking...', 'bot typing');

    try {
      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text })
      });

      const data = await response.json();
      
      // Remove typing indicator
      document.getElementById(typingId)?.remove();

      if (response.ok) {
        addMessage(data.response, 'bot');
      } else {
        addMessage('Sorry, there was an error: ' + (data.detail || 'Unknown error'), 'bot');
      }
    } catch (error) {
      document.getElementById(typingId)?.remove();
      addMessage('Sorry, I couldn\'t connect to the server. Please try again.', 'bot');
    }

    sendBtn.disabled = false;
  }

  function addMessage(text, type) {
    const id = 'msg-' + Date.now();
    const div = document.createElement('div');
    div.id = id;
    div.className = `chatbot-message ${type}`;
    
    // Format bot messages with rich content
    if (type.includes('bot') && !type.includes('typing')) {
      div.innerHTML = formatBotMessage(text);
    } else {
      div.textContent = text;
    }
    
    messages.appendChild(div);
    messages.scrollTop = messages.scrollHeight;
    return id;
  }

  // Format bot messages with markdown-like styling
  function formatBotMessage(text) {
    // Escape HTML first
    let formatted = text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');

    // Code blocks with syntax highlighting hint
    formatted = formatted.replace(/```(\w+)?\n?([\s\S]*?)```/g, (match, lang, code) => {
      const langLabel = lang ? `<span class="code-lang">${lang}</span>` : '';
      return `<div class="code-block">${langLabel}<pre><code>${code.trim()}</code></pre><button class="copy-code-btn" onclick="navigator.clipboard.writeText(this.previousElementSibling.textContent);this.textContent='Copied!';setTimeout(()=>this.textContent='Copy',1500)">Copy</button></div>`;
    });

    // Inline code
    formatted = formatted.replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>');

    // Headers (## Header)
    formatted = formatted.replace(/^### (.+)$/gm, '<h4 class="chat-heading">$1</h4>');
    formatted = formatted.replace(/^## (.+)$/gm, '<h3 class="chat-heading">$1</h3>');
    formatted = formatted.replace(/^# (.+)$/gm, '<h2 class="chat-heading">$1</h2>');

    // Bold text
    formatted = formatted.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    
    // Italic text
    formatted = formatted.replace(/\*([^*]+)\*/g, '<em>$1</em>');

    // Numbered lists
    formatted = formatted.replace(/^(\d+)\. (.+)$/gm, '<li class="numbered-item"><span class="list-num">$1.</span> $2</li>');

    // Bullet points
    formatted = formatted.replace(/^[\-\*] (.+)$/gm, '<li class="bullet-item"><span class="bullet">•</span> $1</li>');

    // Wrap consecutive list items
    formatted = formatted.replace(/(<li class="bullet-item">.*<\/li>\n?)+/g, '<ul class="chat-list">$&</ul>');
    formatted = formatted.replace(/(<li class="numbered-item">.*<\/li>\n?)+/g, '<ol class="chat-list numbered">$&</ol>');

    // Line breaks (preserve double newlines as paragraphs)
    formatted = formatted.replace(/\n\n/g, '</p><p class="chat-para">');
    formatted = formatted.replace(/\n/g, '<br>');

    // Wrap in paragraph if not already structured
    if (!formatted.startsWith('<')) {
      formatted = `<p class="chat-para">${formatted}</p>`;
    }

    // Clean up empty paragraphs
    formatted = formatted.replace(/<p class="chat-para"><\/p>/g, '');
    formatted = formatted.replace(/<p class="chat-para">(<[huo])/g, '$1');
    formatted = formatted.replace(/(<\/[huo][l1-4]?>)<\/p>/g, '$1');

    return formatted;
  }

  // Event listeners
  sendBtn.addEventListener('click', sendMessage);
  input.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') sendMessage();
  });
}
