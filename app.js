/* =========================================================
   SOLIQ CASHBACK — FINAL MINI APP
   Server-side balance, taps, referrals and tasks.
   Referral tasks are automatic. Channel tasks are admin-created.
   ========================================================= */

const tg = window.Telegram?.WebApp || null;

if (tg) {
  tg.ready();
  tg.expand();
  try { tg.setHeaderColor('#09152d'); } catch (_) {}
  try { tg.setBackgroundColor('#050b18'); } catch (_) {}
}

const initData = tg?.initData || '';

// Telegram initData ni query bilan birga header orqali ham yuboramiz.
// Bu encoding/cache sababli paydo bo‘ladigan 401 xatolarni oldini oladi.

const state = {
  user: null,
  tasks: [],
  referral: { referrals: 0, bonus_taps: 0, available_taps: 100, ref_link: '' },
  busy: false,
};

function money(value) {
  return Number(value || 0).toLocaleString('uz-UZ').replace(/,/g, ' ');
}

function apiUrl(path, extra = {}) {
  const params = new URLSearchParams({ initData, ...extra });
  return `${path}?${params.toString()}`;
}

async function api(path, extra = {}) {
  if (!initData) throw new Error('Mini App Telegram ichidan ochilishi kerak.');

  const response = await fetch(apiUrl(path, extra), {
    cache: 'no-store',
    headers: {
      'Accept': 'application/json',
      'X-Telegram-Init-Data': initData
    }
  });

  let data = {};
  try { data = await response.json(); } catch (_) {}

  if (!response.ok || data.ok === false) {
    throw new Error(data.error || data.detail || `Server xatosi (${response.status}).`);
  }

  return data;
}

function alertUser(text) {
  if (tg?.showAlert) tg.showAlert(text);
  else alert(text);
}

function haptic(type = 'light') {
  try { tg?.HapticFeedback?.impactOccurred(type); } catch (_) {}
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, c => ({
    '&':'&amp;', '<':'&lt;', '>':'&gt;', "'":'&#39;', '"':'&quot;'
  }[c]));
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/javascript:/gi, '');
}

function replaceLogos() {
  // Foydalanuvchi yuborgan asli logo.png o‘zgartirilmaydi.
  document.querySelectorAll('img[src="logo.png"]').forEach(img => {
    img.removeAttribute('filter');
    img.style.filter = 'none';
    img.style.webkitFilter = 'none';
  });
}

function renderTapLimit() {
  const tapSection = document.querySelector('.tap-section');
  if (!tapSection || !state.user) return;

  let info = document.getElementById('tapLimitInfo');
  if (!info) {
    info = document.createElement('div');
    info.id = 'tapLimitInfo';
    tapSection.appendChild(info);
  }

  const freeLeft = Math.max(0, Number(state.user.daily_limit || 100) - Number(state.user.taps_today || 0));
  const bonus = Number(state.user.bonus_taps || 0);
  const totalLeft = freeLeft + bonus;

  info.innerHTML = `
    <div>Bugungi bepul tap: <b>${freeLeft}</b> / 100</div>
    <div>Do‘stlardan bonus tap: <b>+${bonus}</b></div>
    <div>Hozir qolgan tap: <b>${totalLeft}</b></div>
  `;
}

function updateUI() {
  const u = state.user;
  if (!u) return;

  setText('balance', money(u.balance));
  setText('withdrawBalance', money(u.balance));
  setText('profileBalance', `${money(u.balance)} keshbek`);
  setText('tapCount', money(u.total_taps));
  setText('refCount', u.referrals);
  setText('level', u.level);
  setText('levelSmall', u.level);
  setText('currentLevel', u.level);
  setText('profileLevel', u.level);
  setText('tapReward', money(100));
  setText('nextLevel', Math.min(Number(u.level) + 1, 5));
  setText('username', u.first_name || 'Foydalanuvchi');
  setText('profileName', u.first_name || 'Foydalanuvchi');

  // Daraja chegaralari o‘zgartirilmaydi.
  const targets = { 1:100000, 2:500000, 3:1500000, 4:5000000, 5:5000000 };
  const previous = { 1:0, 2:100000, 3:500000, 4:1500000, 5:5000000 };
  const level = Number(u.level || 1);
  let progress = level >= 5
    ? 100
    : ((Number(u.balance || 0) - previous[level]) / (targets[level] - previous[level])) * 100;

  progress = Math.max(0, Math.min(100, progress));
  const bar = document.getElementById('progressBar');
  if (bar) bar.style.width = `${progress}%`;
  setText('progressPercent', `${Math.floor(progress)}%`);

  renderTapLimit();
}

async function loadUser() {
  state.user = await api('/api/user');
  updateUI();
}

async function loadReferral() {
  state.referral = await api('/api/referral');
  setText('refCount', state.referral.referrals);
  renderFriends();
}

function renderFriends() {
  const page = document.getElementById('friendsPage');
  if (!page) return;

  const count = Number(state.referral.referrals || 0);
  const bonus = Number(state.referral.bonus_taps || 0);
  const link = state.referral.ref_link || '';

  page.innerHTML = `
    <div class="page-title">Do‘stlar</div>
    <div class="page-subtitle">Har bir haqiqiy taklif sizga qo‘shimcha 100 ta tap beradi.</div>

    <div class="friend-info">
      <div class="friend-icon">👥</div>
      <div>
        <strong>1 do‘st = +100 tap</strong>
        <p>Do‘stingiz botga birinchi marta kirsa, sizga yana 100 ta bonus tap beriladi.</p>
      </div>
    </div>

    <div class="friends-card">
      <div class="balance-label">Taklif qilingan do‘stlar</div>
      <div class="ref-count">${count}</div>
      <div class="ref-label">ta do‘st</div>
      <div class="friend-link">${escapeHtml(link || 'Yuklanmoqda...')}</div>
      <button class="share-button" onclick="shareReferral()">👥 Do‘stlarni taklif qilish</button>
    </div>

    <div class="vip-card">
      <div class="vip-title">⚡ Bonus tap</div>
      <div class="vip-text">Sizda hozir <b>+${bonus}</b> ta qo‘shimcha tap bor.</div>
    </div>

    <div class="friend-info">
      <div class="friend-icon">🎁</div>
      <div>
        <strong>Referral mukofotlari</strong>
        <p>10 ta do‘st — 100 000 so‘m keshbek<br>20 ta do‘st — 200 000 so‘m keshbek<br>30 ta do‘st — 300 000 so‘m keshbek</p>
      </div>
    </div>
  `;
}

async function loadTasks() {
  const data = await api('/api/tasks');
  state.tasks = data.tasks || [];
  renderTasks();
}

function renderTasks() {
  const page = document.getElementById('tasksPage');
  if (!page) return;

  const referralTasks = state.tasks
    .filter(t => t.type === 'referral')
    .sort((a,b) => Number(a.target || 0) - Number(b.target || 0));

  const channelTasks = state.tasks.filter(t => t.type === 'channel');

  let html = `
    <div class="page-title">Vazifalar</div>
    <div class="page-subtitle">Vazifalarni bajaring va so‘m keshbek oling</div>

    <div class="task-section-title">👥 Do‘st taklif qilish</div>
  `;

  // Bu 3 vazifa avtomatik. Admin paneldan qo‘shilmaydi.
  for (const task of referralTasks) {
    const count = Number(state.user?.referrals || 0);
    const target = Number(task.target || 0);
    const ready = count >= target;
    const completed = !!task.completed;

    html += `
      <div class="task-card dynamic-task">
        <div class="task-icon">👥</div>
        <div class="task-content">
          <div class="task-title">${escapeHtml(task.title)}</div>
          <div class="task-reward">+${money(task.reward)} so‘m keshbek oling</div>
          <div class="task-progress">${count} / ${target} ta do‘st</div>
        </div>
        <button class="task-button" onclick="claimReferral(${task.id})" ${completed ? 'disabled' : ''}>
          ${completed ? 'Olindi ✓' : (ready ? 'Olish' : `${Math.max(0, target-count)} ta qoldi`)}
        </button>
      </div>
    `;
  }

  html += `<div class="task-section-title">📢 Kanal vazifalari</div>`;

  if (!channelTasks.length) {
    html += `<div class="empty-task">Admin hali kanal vazifasi qo‘shmagan.</div>`;
  }

  for (const task of channelTasks) {
    const channels = task.channels || (task.channel ? [task.channel] : []);
    const links = task.channel_links || (task.channel_link ? [task.channel_link] : []);

    html += `
      <div class="task-card dynamic-task channel-task">
        <div class="task-icon">📢</div>
        <div class="task-content">
          <div class="task-title">${escapeHtml(task.title)}</div>
          <div class="task-reward">+${money(task.reward)} so‘m keshbek oling</div>
          <div class="channel-list">
            ${task.completed ? '' : channels.map((ch,i) => `
              <a href="${escapeAttr(links[i] || '#')}" target="_blank" rel="noopener" class="channel-link"
                 style="display:inline-flex;align-items:center;justify-content:center;background:#ffffff;color:#17345e;text-decoration:none;border-radius:14px;padding:10px 22px;font-weight:700;min-width:150px;">
                Obuna bo‘lish
              </a>
            `).join('')}
          </div>
        </div>
        <button class="task-button" onclick="checkChannelTask(${task.id})" ${task.completed ? 'disabled' : ''}>
          ${task.completed ? 'Bajarildi ✓' : 'Tekshirish'}
        </button>
      </div>
    `;
  }

  page.innerHTML = html;
}

async function claimReferral(taskId) {
  if (state.busy) return;
  state.busy = true;
  try {
    const data = await api('/api/claim-referral-task', { task_id: taskId });
    await loadUser();
    await loadReferral();
    await loadTasks();
    haptic('medium');
    alertUser(`🎉 +${money(data.reward)} so‘m keshbek olindi!`);
  } catch (e) {
    alertUser(e.message);
  } finally {
    state.busy = false;
  }
}

async function checkChannelTask(taskId) {
  if (state.busy) return;
  state.busy = true;
  try {
    const data = await api('/api/check-channel', { task_id: taskId });
    await loadUser();
    await loadTasks();
    haptic('medium');
    alertUser(`✅ Vazifa bajarildi! +${money(data.reward)} keshbek`);
  } catch (e) {
    alertUser(e.message);
  } finally {
    state.busy = false;
  }
}

let pendingTapCount = 0;
let tapTimer = null;
let tapSending = false;

function tap(event) {
  if (!state.user) return;

  // Server javobini kutmasdan ekranda darhol 100 so‘m qo‘shiladi.
  state.user.balance = Number(state.user.balance || 0) + 100;
  state.user.total_taps = Number(state.user.total_taps || 0) + 1;

  const freeLeft = Math.max(
    0,
    Number(state.user.daily_limit || 100) - Number(state.user.taps_today || 0)
  );

  if (freeLeft > 0) {
    state.user.taps_today = Number(state.user.taps_today || 0) + 1;
  } else if (Number(state.user.bonus_taps || 0) > 0) {
    state.user.bonus_taps = Number(state.user.bonus_taps || 0) - 1;
  } else {
    // Limit tugagan bo‘lsa lokal ko‘rinishni qaytaramiz.
    state.user.balance = Math.max(0, Number(state.user.balance || 0) - 100);
    state.user.total_taps = Math.max(0, Number(state.user.total_taps || 0) - 1);
    haptic('light');
    return;
  }

  pendingTapCount += 1;
  updateUI();
  createPop(
    event?.clientX || window.innerWidth / 2,
    event?.clientY || window.innerHeight / 2,
    '+100'
  );
  haptic('light');

  if (!tapTimer) tapTimer = setTimeout(flushTaps, 5);
}

async function flushTaps() {
  tapTimer = null;
  if (tapSending || pendingTapCount <= 0) return;

  tapSending = true;
  const count = pendingTapCount;
  pendingTapCount = 0;

  try {
    const data = await api('/api/tap', { count });
    state.user = { ...state.user, ...data };
    updateUI();
  } catch (e) {
    try { await loadUser(); } catch (_) {}
  } finally {
    tapSending = false;
    if (pendingTapCount > 0 && !tapTimer) {
      tapTimer = setTimeout(flushTaps, 5);
    }
  }
}

function createPop(x, y, text) {
  const el = document.createElement('div');
  el.className = 'pop';
  el.textContent = `${text} keshbek`;
  el.style.left = `${x}px`;
  el.style.top = `${y}px`;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 800);
}

function openPage(pageId) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  const page = document.getElementById(pageId);
  if (page) page.classList.add('active');

  document.querySelectorAll('.nav-item').forEach(n => {
    n.classList.toggle('active', n.dataset.page === pageId);
  });

  if (pageId === 'tasksPage') loadTasks().catch(e => console.warn(e));
  if (pageId === 'friendsPage') loadReferral().catch(e => console.warn(e));
  window.scrollTo(0, 0);
}

async function shareReferral() {
  if (!state.referral.ref_link) {
    try { await loadReferral(); } catch (e) { alertUser(e.message); return; }
  }

  const finalLink = state.referral.ref_link;
  const text = '🐦 Soliq Keshbek botga qo‘shiling va keshbek ishlang!';
  const share = `https://t.me/share/url?url=${encodeURIComponent(finalLink)}&text=${encodeURIComponent(text)}`;

  try {
    if (tg?.openTelegramLink) tg.openTelegramLink(share);
    else window.open(share, '_blank');
  } catch (_) {
    try {
      await navigator.clipboard.writeText(finalLink);
      alertUser('Referral havola nusxalandi.');
    } catch (_) {
      alertUser(finalLink);
    }
  }
}

async function withdraw() {
  if (!state.user) return;

  const cardEl = document.getElementById('cardInput');
  const amountEl = document.getElementById('amountInput');

  const card = (cardEl?.value || '').trim().replace(/\s+/g, '');
  const amount = Number(amountEl?.value || 0);
  const MIN_WITHDRAW = 600000;

  if (!card) {
    alertUser('❌ Karta raqamingizni kiriting.');
    return;
  }

  if (!/^\d{12,19}$/.test(card)) {
    alertUser('❌ Karta raqami noto‘g‘ri.');
    return;
  }

  if (!amount || amount <= 0) {
    alertUser('❌ Yechiladigan summani kiriting.');
    return;
  }

  if (amount < MIN_WITHDRAW) {
    alertUser('❌ Eng kamida 600 000 so‘m keshbek oling. Vazifalarni bajarib keshbek ishlang.');
    return;
  }

  if (amount > Number(state.user.balance || 0)) {
    alertUser('❌ Balansingiz yetarli emas.');
    return;
  }

  try {
    const response = await fetch(apiUrl('/api/withdraw'), {
      method: 'POST',
      cache: 'no-store',
      headers: {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'X-Telegram-Init-Data': initData
      },
      body: JSON.stringify({ card, amount })
    });

    let data = {};
    try { data = await response.json(); } catch (_) {}

    if (!response.ok || data.ok === false) {
      throw new Error(data.error || data.detail || `Server xatosi (${response.status}).`);
    }

    state.user.balance = Number(data.balance ?? state.user.balance - amount);
    updateUI();

    if (cardEl) cardEl.value = '';
    if (amountEl) amountEl.value = '';

    alertUser(
      `✅ Keshbek yechib olish so‘rovi yuborildi!\n\n` +
      `💰 ${money(data.amount ?? amount)} keshbek\n` +
      `💳 **** ${card.slice(-4)}\n\n` +
      `So‘rovingiz admin tomonidan ko‘rib chiqiladi.`
    );
  } catch (e) {
    alertUser(e.message);
  }
}

window.openPage = openPage;
window.shareReferral = shareReferral;
window.withdraw = withdraw;
window.claimReferral = claimReferral;
window.checkChannelTask = checkChannelTask;

/* ---------- Boot ---------- */

function closeTapTutorial() {
  const el = document.getElementById('tapTutorial');
  if (el) el.style.display = 'none';
  try { localStorage.setItem('tapTutorialSeen', '1'); } catch (_) {}
}
window.closeTapTutorial = closeTapTutorial;

function showTapTutorialOnce() {
  try {
    if (localStorage.getItem('tapTutorialSeen') === '1') return;
  } catch (_) {}
  const el = document.getElementById('tapTutorial');
  if (el) el.style.display = 'flex';
}

document.addEventListener('DOMContentLoaded', async () => {
  replaceLogos();

  const tapButton = document.getElementById('tapButton');
  if (tapButton) tapButton.addEventListener('click', tap);

  try {
    await loadUser();
    await loadReferral();
    await loadTasks();
    openPage('homePage');
    showTapTutorialOnce();
  } catch (e) {
    console.error(e);
    alertUser(e.message);
  }
});


