// WebSocket connection for real-time updates
let ws = null;

function connectWebSocket() {
    ws = new WebSocket(`ws://${window.location.host}/ws`);
    
    ws.onopen = () => {
        console.log('✅ WebSocket connected');
    };
    
    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === 'trade') {
            addTradeToList(data.data);
            showNotification(data.data);
        }
    };
    
    ws.onclose = () => {
        console.log('WebSocket closed, reconnecting...');
        setTimeout(connectWebSocket, 3000);
    };
}

function addTradeToList(trade) {
    const tradesList = document.getElementById('trades-list');
    const tradeCard = document.createElement('div');
    tradeCard.className = 'trade-card';
    
    const flag = trade.side === 'yes' ? '🟢' : '🔴';
    const timestamp = new Date(trade.timestamp).toLocaleString();
    
    tradeCard.innerHTML = `
        <div class="trade-header">
            ${flag} <strong>${trade.ticker}</strong>
        </div>
        <div class="trade-details">
            <span class="trade-amount">$${trade.notional.toLocaleString()}</span>
            <span class="trade-time">${timestamp}</span>
        </div>
    `;
    
    tradesList.prepend(tradeCard);
    
    // Keep only 20 trades
    while (tradesList.children.length > 20) {
        tradesList.removeChild(tradesList.lastChild);
    }
}

function showNotification(trade) {
    if (Notification.permission === 'granted') {
        new Notification('🐋 Whale Trade Alert!', {
            body: `$${trade.notional.toLocaleString()} on ${trade.ticker}`,
            icon: '/static/img/logo.png'
        });
    }
}

// Load initial data
async function loadRecentTrades() {
    const response = await fetch('/api/recent-trades');
    const data = await response.json();
    
    const tradesList = document.getElementById('trades-list');
    tradesList.innerHTML = '';
    
    data.trades.forEach(trade => addTradeToList(trade));
}

// Request notification permission
if ('Notification' in window && Notification.permission === 'default') {
    Notification.requestPermission();
}

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    loadRecentTrades();
    connectWebSocket();
});
