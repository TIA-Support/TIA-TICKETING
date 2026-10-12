/* ═══════════════════════════════════════════════════════════════════════════
   TIA-Solutions Ticketing System — Frontend SPA
   ═══════════════════════════════════════════════════════════════════════════ */

const API = '/api';
let currentUser = null;
let currentTicketId = null;
let editingTicketId = null;
let editingUserId = null;
let ticketPage = 1;
let agentList = [];
let syncTimer = null;
let currentClientId = null;
let clientList = [];

/* ── Helpers ──────────────────────────────────────────────────────────────── */
const token = () => localStorage.getItem('tia_token');
let sessionExpiredHandled = false;

async function apiFetch(path, options = {}) {
  const res = await fetch(API + path, {
    headers: {
      'Content-Type': 'application/json',
      ...(token() ? { Authorization: `Bearer ${token()}` } : {}),
    },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if ((res.status === 401 || res.status === 422) && token()) {
    handleSessionExpired();
    throw new Error('Session expired');
  }
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function handleSessionExpired() {
  if (sessionExpiredHandled) return; // avoid firing repeatedly from concurrent/polled requests
  sessionExpiredHandled = true;
  stopSyncPolling();
  localStorage.removeItem('tia_token');
  currentUser = null;
  hide('app-shell');
  show('auth-screen');
  showLogin();
  showError('login-error', 'Your session expired — please log in again.');
}

function show(id)   { document.getElementById(id)?.classList.remove('hidden'); }
function hide(id)   { document.getElementById(id)?.classList.add('hidden'); }
function el(id)     { return document.getElementById(id); }
function val(id)    { return (el(id)?.value || '').trim(); }
function setVal(id, v) { if (el(id)) el(id).value = v || ''; }
function setText(id, v) { if (el(id)) el(id).textContent = v || ''; }
function setInner(id, h) { if (el(id)) el(id).innerHTML = h; }
function showError(id, msg) { const e=el(id); if(e){e.textContent=msg; e.classList.remove('hidden');} }
function hideError(id)      { el(id)?.classList.add('hidden'); }

function fmt(dt) {
  if (!dt) return '—';
  const raw = String(dt).trim();
  if (!raw) return '—';
  const date = new Date(raw.includes('T') || raw.includes(' ') ? raw : raw + (raw.endsWith('Z') ? '' : 'Z'));
  if (Number.isNaN(date.getTime())) return raw;
  return new Intl.DateTimeFormat('en-ZA', {
    timeZone: 'Africa/Harare',
    day: '2-digit', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  }).format(date);
}

function badge(cls, text) {
  const value = text == null ? '' : String(text);
  const label = value.replace(/_/g, ' ').trim() || '—';
  return `<span class="badge${cls ? ` badge-${cls}` : ''}">${label}</span>`;
}

function categoryLabel(c) {
  const m = { cloud:'Cloud Solutions', network_security:'Network & Cyber Security',
    voip:'VoIP – Voice Solutions', it_support:'IT Support',
    hardware:'Hardware & Maintenance', general:'General Inquiry' };
  return m[c] || c;
}

function supportTypeLabel(s) {
  const m = { remote:'Remote', onsite:'On-site', remote_onsite:'Remote & On-site' };
  return m[s] || s || '—';
}

function slaBadge(t) {
  // Resolved/closed tickets: show whether resolution SLA was ultimately met, not live countdown
  const closed = t.status === 'resolved' || t.status === 'closed';
  if (t.sla_resolution_breached) {
    return `<span class="badge" style="background:#fee2e2;color:#b91c1c;">
      <i class="fa-solid fa-triangle-exclamation mr-1"></i>SLA Breached</span>`;
  }
  if (t.sla_response_breached) {
    return `<span class="badge" style="background:#fef3c7;color:#92400e;">
      <i class="fa-solid fa-clock mr-1"></i>Response Overdue</span>`;
  }
  if (closed) {
    return `<span class="badge" style="background:#dcfce7;color:#166534;">
      <i class="fa-solid fa-check mr-1"></i>Met SLA</span>`;
  }
  return `<span class="badge" style="background:#e0e7ff;color:#3730a3;">
    <i class="fa-solid fa-hourglass-half mr-1"></i>On Track</span>`;
}

/* ── Auth ─────────────────────────────────────────────────────────────────── */
const AUTH_FORMS = ['login-form', 'register-form', 'forgot-password-form', 'reset-password-form'];
function hideAllAuthForms() { AUTH_FORMS.forEach(hide); }

function showLogin() {
  hideAllAuthForms(); show('login-form');
  hideError('login-error'); hideError('reg-error'); hideError('reg-success');
}
function showRegister() { hideAllAuthForms(); show('register-form'); }
function showForgotPassword() {
  hideAllAuthForms(); show('forgot-password-form');
  hideError('forgot-error'); hideError('forgot-success');
  setVal('forgot-email', '');
}

async function doLogin() {
  hideError('login-error');
  try {
    const data = await apiFetch('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email: val('login-email'), password: val('login-password') }),
    });
    localStorage.setItem('tia_token', data.token);
    currentUser = data.user;
    sessionExpiredHandled = false;
    bootApp();
  } catch(e) { showError('login-error', e.message); }
}

async function doRegister() {
  hideError('reg-error'); hideError('reg-success');
  if (!val('reg-name') || !val('reg-email') || !val('reg-company') || !val('reg-password')) {
    return showError('reg-error', 'Name, email, company, and password are required.');
  }
  try {
    await apiFetch('/auth/register', {
      method: 'POST',
      body: JSON.stringify({
        name: val('reg-name'), email: val('reg-email'),
        company: val('reg-company'), phone: val('reg-phone'),
        password: val('reg-password'),
      }),
    });
    el('reg-success').textContent = 'Account created! Please sign in.';
    show('reg-success');
    setTimeout(showLogin, 1500);
  } catch(e) { showError('reg-error', e.message); }
}

async function submitForgotPassword() {
  hideError('forgot-error'); hideError('forgot-success');
  const email = val('forgot-email');
  if (!email) return showError('forgot-error', 'Please enter your email.');
  try {
    const data = await apiFetch('/auth/forgot-password', {
      method: 'POST',
      body: JSON.stringify({ email }),
    });
    el('forgot-success').textContent = data.message || 'A password reset link has been sent to your email.';
    show('forgot-success');
  } catch(e) { showError('forgot-error', e.message); }
}

let resetPasswordToken = null;

async function submitResetPassword() {
  hideError('reset-error'); hideError('reset-success');
  const password = val('reset-password-input');
  const confirm  = val('reset-password-confirm');
  if (!password || password.length < 8) return showError('reset-error', 'Password must be at least 8 characters.');
  if (password !== confirm) return showError('reset-error', 'Passwords do not match.');
  if (!resetPasswordToken) return showError('reset-error', 'This reset link is missing its token — please use the link from your email.');
  try {
    const data = await apiFetch('/auth/reset-password', {
      method: 'POST',
      body: JSON.stringify({ token: resetPasswordToken, password }),
    });
    el('reset-success').textContent = data.message || 'Password reset — please sign in.';
    show('reset-success');
    // Clean the token out of the URL so refreshing doesn't re-show the reset form.
    window.history.replaceState({}, '', '/');
    setTimeout(showLogin, 1800);
  } catch(e) { showError('reset-error', e.message); }
}

function doLogout() {
  stopSyncPolling();
  localStorage.removeItem('tia_token');
  currentUser = null;
  hide('app-shell');
  show('auth-screen');
  showLogin();
}

/* ── Boot ─────────────────────────────────────────────────────────────────── */
async function bootApp() {
  hide('auth-screen');
  show('app-shell');

  // Sidebar user info
  setText('sidebar-name', currentUser.name);
  setText('sidebar-role', currentUser.role);
  setText('header-name', currentUser.name);
  const initials = currentUser.name.split(' ').map(w=>w[0]).join('').toUpperCase().slice(0,2);
  setText('sidebar-avatar', initials);

  // Show staff-only nav items
  if (currentUser.role === 'admin' || currentUser.role === 'technician') { show('nav-users'); }
  if (currentUser.role !== 'client') { show('nav-clients'); show('nav-job-cards'); }

  // Load agents/technicians BEFORE navigating so the assign dropdown is ready
  if (currentUser.role !== 'client') {
    await loadAgents();
  }

  navigate('dashboard');
  pollNotifications();
  startSyncPolling();
}

async function loadAgents() {
  try {
    const [agentsRes, techniciansRes, adminsRes] = await Promise.all([
      apiFetch('/users?role=agent'),
      apiFetch('/users?role=technician'),
      apiFetch('/users?role=admin'),
    ]);
    agentList = [...(agentsRes.users || []), ...(techniciansRes.users || []), ...(adminsRes.users || [])];
  } catch(_){}
}

/* ── Navigation ───────────────────────────────────────────────────────────── */
const VIEWS = ['dashboard','tickets','new-ticket','ticket-detail','users','clients','job-cards','notifications'];

function navigate(view, id = null) {
  VIEWS.forEach(v => hide(`view-${v}`));
  document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));

  const titles = {
    'dashboard':     'Dashboard',
    'tickets':       'Support Tickets',
    'new-ticket':    'Submit New Ticket',
    'ticket-detail': 'Ticket Detail',
    'users':         'User Management',
    'clients':       'Clients',
    'job-cards':     'Job Cards',
    'notifications': 'Notifications',
  };
  setText('page-title', titles[view] || '');

  const navEl = document.querySelector(`[data-nav="${view}"]`);
  if (navEl) navEl.classList.add('active');

  show(`view-${view}`);

  if (view === 'dashboard')      loadDashboard();
  if (view === 'tickets')        loadTickets();
  if (view === 'new-ticket')     resetNewTicket();
  if (view === 'ticket-detail')  loadTicketDetail(id || currentTicketId);
  if (view === 'users')          loadUsers();
  if (view === 'clients')        { closeClientDetail(); loadClients(); }
  if (view === 'job-cards')      { closeJobCardForm(); loadJobCards(); }
  if (view === 'notifications')  loadNotifications();
}

function isUserMidEdit() {
  const modalIds = ['edit-modal', 'user-modal', 'create-user-modal', 'create-client-modal', 'add-contact-modal'];
  if (modalIds.some(id => { const m = el(id); return m && !m.classList.contains('hidden'); })) return true;

  const nc = el('new-comment');
  if (nc && nc.value.trim().length > 0) return true;

  const active = document.activeElement;
  if (active && (active.tagName === 'TEXTAREA' || active.tagName === 'INPUT')) return true;

  return false;
}

function startSyncPolling() {
  stopSyncPolling();
  syncTimer = setInterval(() => {
    if (!currentUser) return;
    if (!el('app-shell') || el('app-shell').classList.contains('hidden')) return;
    if (isUserMidEdit()) return; // don't blow away in-progress typing or an open modal
    if (!el('view-dashboard')?.classList.contains('hidden')) {
      loadDashboard();
    } else if (!el('view-tickets')?.classList.contains('hidden')) {
      loadTickets(ticketPage);
    } else if (!el('view-ticket-detail')?.classList.contains('hidden') && currentTicketId) {
      loadTicketDetail(currentTicketId);
    } else if (!el('view-users')?.classList.contains('hidden')) {
      loadUsers();
    } else if (!el('view-notifications')?.classList.contains('hidden')) {
      loadNotifications();
    }
  }, 90000); // 90s — was 15s
}

function stopSyncPolling() {
  if (syncTimer) { clearInterval(syncTimer); syncTimer = null; }
}

/* ── Dashboard ────────────────────────────────────────────────────────────── */
async function loadDashboard() {
  setInner('dashboard-content', '<div class="flex justify-center py-12"><div class="spinner"></div></div>');
  try {
    const d = await apiFetch('/dashboard');
    setInner('dashboard-content', renderDashboard(d));
  } catch(e) { setInner('dashboard-content', `<p class="text-red-500">${e.message}</p>`); }
}

function renderDashboard(d) {
  const byStatus = d.by_status || {};
  const statCards = [
    { label:'Total',       val: d.total,                    icon:'fa-ticket',          color:'text-tia-600' },
    { label:'Open',        val: byStatus.open || 0,         icon:'fa-folder-open',     color:'text-blue-600' },
    { label:'In Progress', val: byStatus.in_progress || 0,  icon:'fa-gears',           color:'text-yellow-600' },
    { label:'Resolved',    val: byStatus.resolved || 0,     icon:'fa-circle-check',    color:'text-green-600' },
    { label:'SLA Breached', val: d.sla_resolution_breached || 0, icon:'fa-triangle-exclamation', color:'text-red-600' },
    { label:'Response Overdue', val: d.sla_response_breached || 0, icon:'fa-clock', color:'text-amber-600' },
    ...(d.total_users != null ? [{ label:'Users', val: d.total_users, icon:'fa-users', color:'text-purple-600' }] : []),
  ];

  const cards = statCards.map(s => `
    <div class="stat-card">
      <div class="flex items-center justify-between mb-2">
        <span class="text-sm text-gray-500 font-medium">${s.label}</span>
        <i class="fa-solid ${s.icon} ${s.color} text-lg"></i>
      </div>
      <div class="text-3xl font-bold text-gray-800">${s.val}</div>
    </div>`).join('');

  const recentRows = (d.recent || []).map(t => `
    <tr class="ticket-row" onclick="viewTicket(${t.id})">
      <td class="px-4 py-3 text-xs font-mono text-tia-700">${t.ticket_no}</td>
      <td class="px-4 py-3 text-sm">${esc(t.title)}</td>
      <td class="px-4 py-3">${badge(t.status, t.status)}</td>
      <td class="px-4 py-3">${badge(t.priority, t.priority)}</td>
      <td class="px-4 py-3 text-xs text-gray-500">${fmt(t.created_at)}</td>
    </tr>`).join('') || '<tr><td colspan="5" class="px-4 py-8 text-center text-gray-400">No tickets yet</td></tr>';

  return `
    <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-7 gap-4 mb-8">${cards}</div>
    <div class="bg-white rounded-xl border border-gray-200 overflow-hidden">
      <div class="px-5 py-3 border-b border-gray-200 font-medium text-gray-700 text-sm">Recent Tickets</div>
      <table class="w-full text-left">
        <thead class="text-xs text-gray-500 uppercase bg-gray-50">
          <tr>
            <th class="px-4 py-3">Ticket #</th>
            <th class="px-4 py-3">Title</th>
            <th class="px-4 py-3">Status</th>
            <th class="px-4 py-3">Priority</th>
            <th class="px-4 py-3">Created</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100">${recentRows}</tbody>
      </table>
      <div class="px-5 py-3 border-t border-gray-100">
        <button onclick="navigate('tickets')" class="text-sm text-tia-600 hover:underline">View all tickets →</button>
      </div>
    </div>`;
}

/* ── Ticket List ──────────────────────────────────────────────────────────── */
async function loadTickets(page = 1) {
  ticketPage = page;
  const q  = val('search-q');
  const st = val('filter-status');
  const pr = val('filter-priority');
  const ct = val('filter-category');
  const params = new URLSearchParams({ page });
  if (q)  params.set('q',        q);
  if (st) params.set('status',   st);
  if (pr) params.set('priority', pr);
  if (ct) params.set('category', ct);

  setInner('ticket-table-wrap', '<div class="flex justify-center py-12"><div class="spinner"></div></div>');
  try {
    const data = await apiFetch(`/tickets?${params}`);
    renderTicketTable(data);
  } catch(e) { setInner('ticket-table-wrap', `<p class="text-red-500 p-4">${e.message}</p>`); }
}

function renderTicketTable(data) {
  const rows = (data.tickets || []).map(t => `
    <tr class="ticket-row" onclick="viewTicket(${t.id})">
      <td class="px-4 py-3 text-xs font-mono text-tia-700 whitespace-nowrap">${t.ticket_no}</td>
      <td class="px-4 py-3 text-xs font-medium text-gray-600 whitespace-nowrap">${esc(t.request_level || '—')}</td>
      <td class="px-4 py-3">
        <div class="text-sm font-medium text-gray-800">${esc(t.title)}</div>
        <div class="text-xs text-gray-400 mt-0.5">${esc(t.creator_name || '')}</div>
      </td>
      <td class="px-4 py-3">${badge(t.status, t.status)}</td>
      <td class="px-4 py-3 whitespace-nowrap">${slaBadge(t)}</td>
      <td class="px-4 py-3 text-xs text-gray-600">${supportTypeLabel(t.support_type)}</td>
      <td class="px-4 py-3 text-xs text-gray-500 whitespace-nowrap">${fmt(t.created_at)}</td>
      <td class="px-4 py-3 text-xs text-gray-500 whitespace-nowrap">${t.start_time ? fmt(t.start_time) : '—'}</td>
      <td class="px-4 py-3 text-xs text-gray-600 font-medium">${esc(t.hours_worked || '—')}</td>
      <td class="px-4 py-3 text-xs text-gray-500">${esc(t.assignee_name || '—')}</td>
      <td class="px-4 py-3 text-xs text-gray-500">${esc(t.invoice_no || '—')}</td>
    </tr>`).join('') || '<tr><td colspan="11" class="px-4 py-10 text-center text-gray-400">No tickets found</td></tr>';

  setInner('ticket-table-wrap', `
    <div class="overflow-x-auto">
    <table class="w-full text-left" style="min-width:1000px">
      <thead class="text-xs text-gray-500 uppercase bg-gray-50">
        <tr>
          <th class="px-4 py-3">Ticket #</th>
          <th class="px-4 py-3">Level</th>
          <th class="px-4 py-3">Request / Requester</th>
          <th class="px-4 py-3">Status</th>
          <th class="px-4 py-3">SLA</th>
          <th class="px-4 py-3">Remote/On-site</th>
          <th class="px-4 py-3">Logged Time</th>
          <th class="px-4 py-3">Start Time</th>
          <th class="px-4 py-3">Hours</th>
          <th class="px-4 py-3">Attended By</th>
          <th class="px-4 py-3">Invoice</th>
        </tr>
      </thead>
      <tbody class="divide-y divide-gray-100">${rows}</tbody>
    </table>
    </div>`);

  // Pagination
  const total = data.total || 0;
  const pages = data.pages || 1;
  const page  = data.page  || 1;
  const pageBtns = Array.from({length: Math.min(pages, 7)}, (_,i) => {
    const p = i + 1;
    return `<button onclick="loadTickets(${p})"
      class="px-3 py-1 rounded text-sm ${p===page ? 'bg-tia-600 text-white' : 'border border-gray-300 text-gray-600 hover:bg-gray-50'}">${p}</button>`;
  }).join('');
  setInner('ticket-pagination', `
    <span>Showing ${data.tickets.length} of ${total} tickets</span>
    <div class="flex gap-1">${pageBtns}</div>`);
}

/* ── New Ticket ───────────────────────────────────────────────────────────── */
function resetNewTicket() {
  hideError('new-ticket-error');
  ['nt-title','nt-description','nt-new-client-name','nt-new-contact-name',
   'nt-new-contact-email','nt-new-contact-phone','nt-new-contact-password'].forEach(id => setVal(id,''));
  setVal('nt-category','it_support');
  setVal('nt-priority','medium');
  hide('nt-new-client-fields');
  hide('nt-new-contact-fields');

  if (currentUser.role !== 'client') {
    show('nt-behalf-wrap');
    populateNewTicketClients();
    show('nt-assign-wrap');
    populateNewTicketAssignees();
  } else {
    hide('nt-behalf-wrap');
    hide('nt-assign-wrap');
  }
}

async function populateNewTicketClients() {
  try {
    if (clientList.length === 0) {
      const data = await apiFetch('/clients');
      clientList = data.clients || [];
    }
    const sel = el('nt-client');
    if (!sel) return;
    sel.innerHTML = '<option value="">— Log as myself —</option>' +
      '<option value="__new_client__">+ Add New Client…</option>' +
      clientList.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join('');
    el('nt-contact').innerHTML = '<option value="">— Select a client first —</option>';
  } catch(_) {}
}

async function populateNewTicketAssignees() {
  const sel = el('nt-assigned-to');
  if (!sel) return;
  try {
    if (agentList.length === 0) {
      await loadAgents();
    }
    sel.innerHTML = '<option value="">— Unassigned —</option>' +
      agentList.map(a => `<option value="${a.id}">${esc(a.name)} (${a.role})</option>`).join('');
  } catch(_) {}
}

async function onNewTicketClientChange() {
  const clientId = val('nt-client');
  const sel = el('nt-contact');

  if (clientId === '__new_client__') {
    // Brand new company — no existing contacts possible, go straight to new-contact fields.
    sel.innerHTML = '<option value="">— New contact below —</option>';
    show('nt-new-client-fields');
    show('nt-new-contact-fields');
    return;
  }
  hide('nt-new-client-fields');

  if (!clientId) {
    sel.innerHTML = '<option value="">— Select a client first —</option>';
    hide('nt-new-contact-fields');
    return;
  }

  sel.innerHTML = '<option value="">Loading…</option>';
  try {
    const client = await apiFetch(`/clients/${clientId}`);
    const contacts = client.contacts || [];
    sel.innerHTML =
      '<option value="">— Select a contact —</option>' +
      '<option value="__new_contact__">+ Add New Contact…</option>' +
      contacts.map(c => `<option value="${c.id}">${esc(c.name)} (${esc(c.email)})</option>`).join('');
  } catch(e) {
    sel.innerHTML = '<option value="">Failed to load contacts</option>';
  }
  onNewTicketContactChange();
}

function onNewTicketContactChange() {
  const contactVal = val('nt-contact');
  if (contactVal === '__new_contact__') {
    show('nt-new-contact-fields');
  } else if (val('nt-client') !== '__new_client__') {
    hide('nt-new-contact-fields');
  }
}

async function submitTicket() {
  hideError('new-ticket-error');
  const title       = val('nt-title');
  const description = val('nt-description');
  if (!title || !description) {
    return showError('new-ticket-error', 'Title and description are required.');
  }

  const isStaff       = currentUser.role !== 'client';
  const clientChoice  = isStaff ? val('nt-client')  : '';
  const contactChoice = isStaff ? val('nt-contact') : '';
  const assignedTo    = isStaff ? val('nt-assigned-to') : '';

  let onBehalfOf = contactChoice && contactChoice !== '__new_contact__' ? contactChoice : '';

  try {
    // Step 1: create a brand new client company, if requested
    let targetClientId = (clientChoice && clientChoice !== '__new_client__') ? clientChoice : null;
    if (clientChoice === '__new_client__') {
      const newClientName = val('nt-new-client-name');
      if (!newClientName) return showError('new-ticket-error', 'New company name is required.');
      const newClient = await apiFetch('/clients', {
        method: 'POST',
        body: JSON.stringify({ name: newClientName }),
      });
      targetClientId = newClient.id;
      clientList = []; // force refresh elsewhere (Clients tab) next time it loads
    }

    // Step 2: create a new contact under that client, if requested
    if (clientChoice === '__new_client__' || contactChoice === '__new_contact__') {
      const ncName     = val('nt-new-contact-name');
      const ncEmail    = val('nt-new-contact-email');
      const ncPassword = val('nt-new-contact-password');
      if (!ncName || !ncEmail || !ncPassword) {
        return showError('new-ticket-error', 'New contact name, email, and password are required.');
      }
      const newContact = await apiFetch(`/clients/${targetClientId}/users`, {
        method: 'POST',
        body: JSON.stringify({
          name: ncName, email: ncEmail,
          phone: val('nt-new-contact-phone'), password: ncPassword,
        }),
      });
      onBehalfOf = newContact.id;
    }

    const t = await apiFetch('/tickets', {
      method: 'POST',
      body: JSON.stringify({
        title, description,
        category:      val('nt-category'),
        priority:      val('nt-priority'),
        request_level: val('nt-request-level'),
        support_type:  val('nt-support-type'),
        ...(onBehalfOf ? { on_behalf_of: parseInt(onBehalfOf) } : {}),
        ...(assignedTo ? { assigned_to: parseInt(assignedTo) } : {}),
      }),
    });
    navigate('ticket-detail', t.id);
  } catch(e) { showError('new-ticket-error', e.message); }
}

/* ── Ticket Detail ────────────────────────────────────────────────────────── */
function viewTicket(id) {
  currentTicketId = id;
  navigate('ticket-detail', id);
}

async function loadTicketDetail(id) {
  setInner('ticket-detail-content', '<div class="flex justify-center py-12"><div class="spinner"></div></div>');
  try {
    // Ensure agents are loaded before rendering so the assign dropdown is populated
    if (agentList.length === 0 && currentUser.role !== 'client') {
      await loadAgents();
    }
    const t = await apiFetch(`/tickets/${id}`);
    setInner('ticket-detail-content', renderTicketDetail(t));
  } catch(e) { setInner('ticket-detail-content', `<p class="text-red-500">${e.message}</p>`); }
}

function renderTicketDetail(t) {
  const isStaff = currentUser.role !== 'client';
  const comments = (t.comments || []).map(c => {
    const isMe      = c.user_id === currentUser.id;
    const cls       = c.is_internal ? 'comment-internal' : isMe ? 'comment-me' : 'comment-them';
    const internalTag = c.is_internal ? '<span class="text-xs bg-yellow-100 text-yellow-700 px-2 py-0.5 rounded ml-2">Internal Note</span>' : '';
    return `
      <div class="${cls} rounded-lg p-4 mb-3">
        <div class="flex items-center gap-2 mb-2">
          <span class="font-medium text-sm">${esc(c.author_name)}</span>
          ${badge(c.author_role, c.author_role)}
          ${internalTag}
          <span class="text-xs text-gray-400 ml-auto">${fmt(c.created_at)}</span>
        </div>
        <p class="text-sm text-gray-700 whitespace-pre-wrap">${esc(c.body)}</p>
      </div>`;
  }).join('') || '<p class="text-gray-400 text-sm text-center py-6">No replies yet.</p>';

  const internalCheckbox = isStaff ? `
    <div class="flex items-center gap-2 mt-2">
      <input id="comment-internal" type="checkbox" class="rounded" />
      <label for="comment-internal" class="text-sm text-gray-600">Internal note (not visible to client)</label>
    </div>` : '';

  const editBtn = (isStaff || t.created_by === currentUser.id) ? `
    <button onclick="openEditModal(${t.id})" class="border border-gray-300 text-gray-600 hover:bg-gray-50 px-4 py-1.5 rounded-lg text-sm transition">
      <i class="fa-solid fa-pen mr-1"></i>Edit
    </button>` : '';

  const deleteBtn = currentUser.role === 'admin' ? `
    <button onclick="deleteTicket(${t.id})" class="border border-red-200 text-red-600 hover:bg-red-50 px-4 py-1.5 rounded-lg text-sm transition ml-2">
      <i class="fa-solid fa-trash mr-1"></i>Delete
    </button>` : '';

  return `
    <div class="flex items-center gap-3 mb-5">
      <button onclick="navigate('tickets')" class="text-tia-600 hover:underline text-sm flex items-center gap-1">
        <i class="fa-solid fa-chevron-left text-xs"></i> All Tickets
      </button>
    </div>
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-5">
      <!-- Left: main detail -->
      <div class="lg:col-span-2 space-y-5">
        <div class="bg-white rounded-xl border border-gray-200 p-5">
          <div class="flex items-start justify-between gap-4 mb-4">
            <div>
              <span class="text-xs font-mono text-tia-600 font-medium">${t.ticket_no}</span>
              <h2 class="text-xl font-bold text-gray-800 mt-1">${esc(t.title)}</h2>
            </div>
            <div class="flex gap-2 flex-shrink-0">
              ${editBtn}${deleteBtn}
            </div>
          </div>
          <div class="flex flex-wrap gap-2 mb-5">
            ${badge(t.status, t.status)}
            ${badge(t.priority, t.priority)}
            <span class="badge cat-${t.category} badge">${categoryLabel(t.category)}</span>
          </div>
          <div class="text-sm text-gray-700 leading-relaxed whitespace-pre-wrap">${esc(t.description)}</div>
        </div>

        <!-- Comments -->
        <div class="bg-white rounded-xl border border-gray-200 p-5">
          <h3 class="font-semibold text-gray-700 mb-4">Replies</h3>
          <div id="comments-wrap">${comments}</div>
          <div class="mt-4 border-t border-gray-100 pt-4">
            <textarea id="new-comment" rows="3" placeholder="Type your reply…"
              class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-tia-500 resize-none"></textarea>
            ${internalCheckbox}
            <div id="comment-error" class="hidden mt-2 text-red-600 text-sm"></div>
            <button onclick="addComment(${t.id})" class="mt-3 bg-tia-600 hover:bg-tia-700 text-white px-5 py-2 rounded-lg text-sm font-medium transition">
              <i class="fa-solid fa-reply mr-1"></i>Send Reply
            </button>
          </div>
        </div>
      </div>

      <!-- Right: meta -->
      <div class="space-y-4">
        <div class="bg-white rounded-xl border border-gray-200 p-5 space-y-3">
          <h3 class="font-semibold text-gray-700 text-sm border-b border-gray-100 pb-2 flex items-center justify-between">
            SLA Status ${slaBadge(t)}
          </h3>
          <div class="text-sm">
            <div class="text-gray-500 text-xs mb-0.5">First Response Due</div>
            <div class="${t.sla_response_breached && !t.first_response_at ? 'text-red-600 font-medium' : ''}">${t.sla_response_due ? fmt(t.sla_response_due) : '—'}</div>
            ${t.first_response_at ? `<div class="text-xs text-gray-400 mt-0.5">Responded: ${fmt(t.first_response_at)}</div>` : ''}
          </div>
          <div class="text-sm">
            <div class="text-gray-500 text-xs mb-0.5">Resolution Due</div>
            <div class="${t.sla_resolution_breached && !t.resolved_at ? 'text-red-600 font-medium' : ''}">${t.sla_resolution_due ? fmt(t.sla_resolution_due) : '—'}</div>
            ${t.resolved_at ? `<div class="text-xs text-gray-400 mt-0.5">Resolved: ${fmt(t.resolved_at)}</div>` : ''}
          </div>
        </div>

        <div class="bg-white rounded-xl border border-gray-200 p-5 space-y-3">
          <h3 class="font-semibold text-gray-700 text-sm border-b border-gray-100 pb-2">Ticket Info</h3>
          <div class="text-sm">
            <div class="text-gray-500 text-xs mb-0.5">Submitted by</div>
            <div class="font-medium">${esc(t.creator_name || '—')}</div>
            <div class="text-gray-500 text-xs">${esc(t.creator_email || '')}</div>
            ${t.creator_company ? `<div class="text-gray-500 text-xs">${esc(t.creator_company)}</div>` : ''}
            ${t.creator_phone   ? `<div class="text-gray-500 text-xs">${esc(t.creator_phone)}</div>`   : ''}
          </div>
          <div class="text-sm">
            <div class="text-gray-500 text-xs mb-0.5">Attended By</div>
            <div class="font-medium">${esc(t.assignee_name || 'Unassigned')}</div>
          </div>
          <div class="text-sm">
            <div class="text-gray-500 text-xs mb-0.5">Request Level</div>
            <div class="font-medium">${esc(t.request_level || '—')}</div>
          </div>
          <div class="text-sm">
            <div class="text-gray-500 text-xs mb-0.5">Remote / On-site</div>
            <div class="font-medium">${supportTypeLabel(t.support_type)}</div>
          </div>
          <div class="text-sm">
            <div class="text-gray-500 text-xs mb-0.5">Request Logged</div>
            <div>${fmt(t.created_at)}</div>
          </div>
          <div class="text-sm">
            <div class="text-gray-500 text-xs mb-0.5">Start Time</div>
            <div>${t.start_time ? fmt(t.start_time) : '—'}</div>
          </div>
          <div class="text-sm">
            <div class="text-gray-500 text-xs mb-0.5">End Time</div>
            <div>${t.end_time ? fmt(t.end_time) : '—'}</div>
          </div>
          <div class="text-sm">
            <div class="text-gray-500 text-xs mb-0.5">Hours Worked</div>
            <div class="font-medium">${esc(t.hours_worked || '—')}</div>
          </div>
          <div class="text-sm">
            <div class="text-gray-500 text-xs mb-0.5">Invoice No.</div>
            <div class="font-medium">${esc(t.invoice_no || '—')}</div>
          </div>
        </div>

        ${t.work_implemented ? `
        <div class="bg-white rounded-xl border border-gray-200 p-5">
          <h3 class="font-semibold text-gray-700 text-sm border-b border-gray-100 pb-2 mb-3">Work Implemented</h3>
          <p class="text-sm text-gray-700 whitespace-pre-wrap">${esc(t.work_implemented)}</p>
        </div>` : ''}

        <!-- Quick Actions (staff only) -->
        ${isStaff ? `
        <div class="bg-white rounded-xl border border-gray-200 p-5 space-y-3">
          <h3 class="font-semibold text-gray-700 text-sm border-b border-gray-100 pb-2">Quick Actions</h3>
          <div>
            <label class="text-xs text-gray-500 mb-1 block">Change Status</label>
            <select id="qs-status" onchange="quickUpdateStatus(${t.id},this.value)"
              class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-tia-500">
              ${['open','in_progress','pending','resolved','closed'].map(s =>
                `<option value="${s}" ${t.status===s?'selected':''}>${s.replace('_',' ')}</option>`
              ).join('')}
            </select>
          </div>
          <div>
            <label class="text-xs text-gray-500 mb-1 block">Assign To Technician</label>
            <select id="qs-assign" onchange="quickAssign(${t.id},this.value)"
              class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-tia-500">
              <option value="">— Unassigned —</option>
              ${agentList.map(a => `<option value="${a.id}" ${t.assigned_to===a.id?'selected':''}>${esc(a.name)} (${a.role})</option>`).join('')}
            </select>
          </div>
        </div>` : ''}
      </div>
    </div>`;
}

async function addComment(ticketId) {
  hideError('comment-error');
  const body = val('new-comment');
  if (!body) return showError('comment-error', 'Reply cannot be empty.');
  const is_internal = el('comment-internal')?.checked ? 1 : 0;
  try {
    await apiFetch(`/comments/${ticketId}`, {
      method: 'POST',
      body: JSON.stringify({ body, is_internal }),
    });
    loadTicketDetail(ticketId);
  } catch(e) { showError('comment-error', e.message); }
}

async function quickUpdateStatus(ticketId, status) {
  try { await apiFetch(`/tickets/${ticketId}`, { method:'PUT', body:JSON.stringify({status}) }); }
  catch(e) { alert(e.message); }
}

async function quickAssign(ticketId, assignedTo) {
  try {
    await apiFetch(`/tickets/${ticketId}`, {
      method: 'PUT',
      body: JSON.stringify({ assigned_to: assignedTo ? parseInt(assignedTo) : null }),
    });
    // Refresh the assignee label in the info panel without full reload
    loadTicketDetail(ticketId);
  } catch(e) { alert(e.message); }
}

/* ── Edit Ticket Modal ────────────────────────────────────────────────────── */
function openEditModal(id) {
  editingTicketId = id;
  hideError('edit-error');
  apiFetch(`/tickets/${id}`).then(t => {
    setVal('edit-title',            t.title);
    setVal('edit-status',           t.status);
    setVal('edit-priority',         t.priority);
    setVal('edit-request-level',    t.request_level  || 'Level 1');
    setVal('edit-support-type',     t.support_type   || 'remote');
    setVal('edit-work-implemented', t.work_implemented || '');
    setVal('edit-start-time',       toDatetimeLocal(t.start_time));
    setVal('edit-end-time',         toDatetimeLocal(t.end_time));
    setVal('edit-hours-worked',     t.hours_worked   || '');
    setVal('edit-invoice-no',       t.invoice_no     || '');
    setVal('edit-description',      t.description);

    // Populate assignee dropdown — visible to both admin and agent
    const sel = el('edit-assigned');
    if (agentList.length === 0) {
      // Fetch fresh if list is empty (e.g. technician role logged in)
      apiFetch('/users?role=agent').then(d => {
        agentList = d.users || [];
        return apiFetch('/users?role=technician');
      }).then(d => {
        agentList = [...agentList, ...(d.users || [])];
        return apiFetch('/users?role=admin');
      }).then(d => {
        agentList = [...agentList, ...(d.users || [])];
        sel.innerHTML = buildAssigneeOptions(t.assigned_to);
      }).catch(() => {});
    } else {
      sel.innerHTML = buildAssigneeOptions(t.assigned_to);
    }
    show('edit-modal');
  });
}

function buildAssigneeOptions(currentAssignedTo) {
  return '<option value="">\u2014 Unassigned \u2014</option>' +
    agentList.map(a =>
      `<option value="${a.id}" ${a.id === currentAssignedTo ? 'selected' : ''}>${esc(a.name)} (${a.role})</option>`
    ).join('');
}

function formatHarareInputValue(date) {
  const formatter = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Africa/Harare',
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  });
  const parts = formatter.formatToParts(date);
  const values = Object.fromEntries(parts.filter(p => p.type !== 'literal').map(p => [p.type, p.value]));
  return `${values.year}-${values.month}-${values.day}T${values.hour}:${values.minute}`;
}

function toDatetimeLocal(dt) {
  if (!dt) return '';
  const raw = String(dt).trim();
  if (!raw) return '';
  try {
    const date = new Date(raw.includes('T') || raw.includes(' ') ? raw : raw + (raw.endsWith('Z') ? '' : 'Z'));
    if (Number.isNaN(date.getTime())) return raw.slice(0, 16);
    return formatHarareInputValue(date);
  } catch(_) { return raw.slice(0, 16); }
}

function parseHarareDateTime(value) {
  if (!value) return null;
  const raw = String(value).trim();
  if (!raw) return null;
  const [datePart, timePart = '00:00'] = raw.split('T');
  const [year, month, day] = datePart.split('-').map(Number);
  const [hour, minute] = timePart.split(':').map(Number);
  return new Date(Date.UTC(year, month - 1, day, hour, minute, 0) - 2 * 60 * 60 * 1000);
}

function autoCalcHours() {
  const start = parseHarareDateTime(el('edit-start-time')?.value);
  const end = parseHarareDateTime(el('edit-end-time')?.value);
  if (!start || !end) return;
  const diff = end - start;
  if (diff <= 0) return;
  const totalMins = Math.round(diff / 60000);
  const h = Math.floor(totalMins / 60);
  const m = totalMins % 60;
  setVal('edit-hours-worked', h > 0 ? `${h} Hour${h > 1 ? 's' : ''} ${m} Minute${m !== 1 ? 's' : ''}` : `${m} Minute${m !== 1 ? 's' : ''}`);
}

function closeEditModal() { hide('edit-modal'); editingTicketId = null; }

async function saveEdit() {
  hideError('edit-error');
  if ((val('edit-status') === 'resolved' || val('edit-status') === 'closed') && !val('edit-end-time')) {
    setVal('edit-end-time', formatHarareInputValue(new Date()));
  }
  autoCalcHours();
  const payload = {
    title:             val('edit-title'),
    status:            val('edit-status'),
    priority:          val('edit-priority'),
    request_level:     val('edit-request-level'),
    support_type:      val('edit-support-type'),
    work_implemented:  val('edit-work-implemented'),
    start_time:        val('edit-start-time'),
    end_time:          val('edit-end-time'),
    hours_worked:      val('edit-hours-worked'),
    invoice_no:        val('edit-invoice-no'),
    description:       val('edit-description'),
  };
  // Agents and admins can assign
  if (currentUser.role !== 'client') {
    const a = val('edit-assigned');
    payload.assigned_to = a ? parseInt(a) : null;
  }
  try {
    await apiFetch(`/tickets/${editingTicketId}`, { method:'PUT', body:JSON.stringify(payload) });
    closeEditModal();
    loadTicketDetail(editingTicketId);
  } catch(e) { showError('edit-error', e.message); }
}

async function deleteTicket(id) {
  if (!confirm('Are you sure you want to delete this ticket?')) return;
  try {
    await apiFetch(`/tickets/${id}`, { method: 'DELETE' });
    navigate('tickets');
  } catch(e) { alert(e.message); }
}

/* ── Users ────────────────────────────────────────────────────────────────── */
async function loadUsers() {
  const q    = val('user-search');
  const role = val('user-role-filter');
  const params = new URLSearchParams();
  if (q)    params.set('q',    q);
  if (role) params.set('role', role);

  setInner('user-table-wrap', '<div class="flex justify-center py-12"><div class="spinner"></div></div>');
  try {
    const data = await apiFetch(`/users?${params}`);
    renderUserTable(data.users || []);
  } catch(e) { setInner('user-table-wrap', `<p class="text-red-500 p-4">${e.message}</p>`); }
}

function renderUserTable(users) {
  const rows = users.map(u => `
    <tr>
      <td class="px-4 py-3 text-sm font-medium text-gray-800">${esc(u.name)}</td>
      <td class="px-4 py-3 text-sm text-gray-600">${esc(u.email)}</td>
      <td class="px-4 py-3">${badge(u.role, u.role)}</td>
      <td class="px-4 py-3 text-sm text-gray-500">${esc(u.company || '—')}</td>
      <td class="px-4 py-3 text-xs text-gray-400">${fmt(u.created_at)}</td>
      <td class="px-4 py-3 text-right">
        <button onclick="openUserModal(${u.id},'${esc(u.name)}','${esc(u.company||'')}','${esc(u.phone||'')}','${u.role}')"
          class="text-tia-600 hover:underline text-sm mr-3">Edit</button>
        ${u.id !== currentUser.id ? `<button onclick="deleteUser(${u.id})" class="text-red-500 hover:underline text-sm">Delete</button>` : ''}
      </td>
    </tr>`).join('') || '<tr><td colspan="6" class="px-4 py-10 text-center text-gray-400">No users found</td></tr>';

  setInner('user-table-wrap', `
    <table class="w-full text-left">
      <thead class="text-xs text-gray-500 uppercase bg-gray-50">
        <tr>
          <th class="px-4 py-3">Name</th>
          <th class="px-4 py-3">Email</th>
          <th class="px-4 py-3">Role</th>
          <th class="px-4 py-3">Company</th>
          <th class="px-4 py-3">Joined</th>
          <th class="px-4 py-3 text-right">Actions</th>
        </tr>
      </thead>
      <tbody class="divide-y divide-gray-100">${rows}</tbody>
    </table>`);
}

function openCreateUserModal() {
  hideError('create-user-error');
  setVal('cu-name', '');
  setVal('cu-email', '');
  setVal('cu-company', '');
  setVal('cu-phone', '');
  setVal('cu-password', '');
  const roleSelect = el('cu-role');
  if (roleSelect) {
    const adminOption = Array.from(roleSelect.options).find(o => o.value === 'admin');
    if (currentUser.role !== 'admin' && adminOption) roleSelect.removeChild(adminOption);
    if (currentUser.role === 'admin' && !adminOption) {
      const opt = document.createElement('option');
      opt.value = 'admin';
      opt.textContent = 'Admin';
      roleSelect.appendChild(opt);
    }
    roleSelect.value = 'client';
  }
  show('create-user-modal');
}

function closeCreateUserModal() { hide('create-user-modal'); }

async function createUser() {
  hideError('create-user-error');
  const payload = {
    name: val('cu-name'),
    email: val('cu-email'),
    company: val('cu-company'),
    phone: val('cu-phone'),
    role: val('cu-role'),
    password: val('cu-password'),
  };
  try {
    await apiFetch('/users', { method: 'POST', body: JSON.stringify(payload) });
    closeCreateUserModal();
    loadUsers();
  } catch(e) { showError('create-user-error', e.message); }
}

function openUserModal(id, name, company, phone, role) {
  editingUserId = id;
  hideError('user-modal-error');
  setVal('um-name',     name);
  setVal('um-company',  company);
  setVal('um-phone',    phone);
  setVal('um-role',     role);
  setVal('um-password', '');
  show('user-modal');
}

function closeUserModal() { hide('user-modal'); editingUserId = null; }

async function saveUserEdit() {
  hideError('user-modal-error');
  const payload = {
    name:    val('um-name'),
    company: val('um-company'),
    phone:   val('um-phone'),
    role:    val('um-role'),
  };
  const pw = val('um-password');
  if (pw) payload.password = pw;
  try {
    await apiFetch(`/users/${editingUserId}`, { method:'PUT', body:JSON.stringify(payload) });
    closeUserModal();
    loadUsers();
    if (editingUserId === currentUser.id) {
      currentUser.name = payload.name;
      setText('sidebar-name', currentUser.name);
      setText('header-name', currentUser.name);
    }
  } catch(e) { showError('user-modal-error', e.message); }
}

async function deleteUser(id) {
  if (!confirm('Delete this user? This cannot be undone.')) return;
  try { await apiFetch(`/users/${id}`, { method:'DELETE' }); loadUsers(); }
  catch(e) { alert(e.message); }
}

/* ── Notifications ────────────────────────────────────────────────────────── */
async function loadNotifications() {
  setInner('notif-list', '<div class="flex justify-center py-8"><div class="spinner"></div></div>');
  try {
    const data = await apiFetch('/users/notifications');
    const notifs = data.notifications || [];
    if (!notifs.length) {
      setInner('notif-list', '<p class="text-center text-gray-400 py-8">No notifications</p>');
      return;
    }
    setInner('notif-list', notifs.map(n => `
      <div class="px-5 py-4 flex items-start gap-3 ${n.is_read ? '' : 'bg-blue-50'}">
        <div class="w-2 h-2 rounded-full mt-2 flex-shrink-0 ${n.is_read ? 'bg-gray-300' : 'bg-tia-500'}"></div>
        <div class="flex-1 min-w-0">
          <p class="text-sm text-gray-800">${esc(n.message)}</p>
          <p class="text-xs text-gray-400 mt-0.5">${fmt(n.created_at)}</p>
        </div>
        ${n.link ? `<button onclick="navigateFromLink('${n.link}')" class="text-tia-600 text-xs hover:underline flex-shrink-0">View</button>` : ''}
      </div>`).join(''));
  } catch(e) { setInner('notif-list', `<p class="text-red-500 p-4">${e.message}</p>`); }
}

function navigateFromLink(link) {
  const m = link.match(/\/ticket\/(\d+)/);
  if (m) viewTicket(parseInt(m[1]));
}

async function markAllRead() {
  try { await apiFetch('/users/notifications/read', { method:'POST' }); loadNotifications(); updateBadge(); }
  catch(_){}
}

async function pollNotifications() {
  updateBadge();
  setInterval(updateBadge, 90000); // 90s — was 30s
}

async function updateBadge() {
  try {
    const data = await apiFetch('/users/notifications');
    const unread = (data.notifications || []).filter(n => !n.is_read).length;
    const badge = el('notif-badge');
    if (unread > 0) {
      badge.textContent = unread > 9 ? '9+' : unread;
      badge.classList.remove('hidden');
    } else {
      badge.classList.add('hidden');
    }
  } catch(_){}
}

/* ── Security: HTML escaping ─────────────────────────────────────────────── */
function esc(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;')
    .replace(/'/g,'&#39;');
}

/* ── Keyboard shortcuts ─────────────────────────────────────────────────── */
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    closeEditModal();
    closeUserModal();
  }
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
    if (!el('edit-modal').classList.contains('hidden'))  saveEdit();
    if (!el('user-modal').classList.contains('hidden'))  saveUserEdit();
  }
});

/* ── Clients ──────────────────────────────────────────────────────────────── */
async function loadClients() {
  const q = val('client-search');
  const params = new URLSearchParams();
  if (q) params.set('q', q);

  setInner('client-table-wrap', '<div class="flex justify-center py-12"><div class="spinner"></div></div>');
  try {
    const data = await apiFetch(`/clients?${params}`);
    clientList = data.clients || [];
    renderClientTable(clientList);
  } catch(e) { setInner('client-table-wrap', `<p class="text-red-500 p-4">${e.message}</p>`); }
}

function renderClientTable(clients) {
  const rows = clients.map(c => `
    <tr>
      <td class="px-4 py-3 text-sm font-medium text-gray-800">
        <button onclick="openClientDetail(${c.id})" class="text-tia-600 hover:underline">${esc(c.name)}</button>
      </td>
      <td class="px-4 py-3 text-sm text-gray-600">${c.contact_count} contact${c.contact_count == 1 ? '' : 's'}</td>
      <td class="px-4 py-3 text-sm text-gray-500">${esc(c.notes || '—')}</td>
      <td class="px-4 py-3 text-xs text-gray-400">${fmt(c.created_at)}</td>
      <td class="px-4 py-3 text-right">
        <button onclick="openClientDetail(${c.id})" class="text-tia-600 hover:underline text-sm mr-3">View</button>
        ${currentUser.role === 'admin' ? `<button onclick="deleteClient(${c.id})" class="text-red-500 hover:underline text-sm">Delete</button>` : ''}
      </td>
    </tr>`).join('') || '<tr><td colspan="5" class="px-4 py-10 text-center text-gray-400">No clients found</td></tr>';

  setInner('client-table-wrap', `
    <table class="w-full text-left">
      <thead class="text-xs text-gray-500 uppercase bg-gray-50">
        <tr>
          <th class="px-4 py-3">Company</th>
          <th class="px-4 py-3">Contacts</th>
          <th class="px-4 py-3">Notes</th>
          <th class="px-4 py-3">Added</th>
          <th class="px-4 py-3 text-right">Actions</th>
        </tr>
      </thead>
      <tbody class="divide-y divide-gray-100">${rows}</tbody>
    </table>`);
}

function openCreateClientModal() {
  hideError('create-client-error');
  setVal('cc-name', '');
  setVal('cc-notes', '');
  show('create-client-modal');
}
function closeCreateClientModal() { hide('create-client-modal'); }

async function createClient() {
  hideError('create-client-error');
  try {
    await apiFetch('/clients', {
      method: 'POST',
      body: JSON.stringify({ name: val('cc-name'), notes: val('cc-notes') }),
    });
    closeCreateClientModal();
    clientList = []; // force refresh next time new-ticket picker loads
    loadClients();
  } catch(e) { showError('create-client-error', e.message); }
}

async function deleteClient(id) {
  if (!confirm('Delete this client? This only works if it has no contacts.')) return;
  try {
    await apiFetch(`/clients/${id}`, { method: 'DELETE' });
    clientList = [];
    loadClients();
  } catch(e) { alert(e.message); }
}

async function openClientDetail(id) {
  currentClientId = id;
  hide('client-table-wrap');
  show('client-detail-panel');
  setText('client-detail-name', 'Loading…');
  setInner('client-contacts-wrap', '<div class="flex justify-center py-12"><div class="spinner"></div></div>');
  try {
    const client = await apiFetch(`/clients/${id}`);
    setText('client-detail-name', client.name);
    renderClientContacts(client.contacts || []);
  } catch(e) { setInner('client-contacts-wrap', `<p class="text-red-500 p-4">${e.message}</p>`); }
}

function closeClientDetail() {
  currentClientId = null;
  hide('client-detail-panel');
  show('client-table-wrap');
}

function renderClientContacts(contacts) {
  const rows = contacts.map(c => `
    <tr>
      <td class="px-4 py-3 text-sm font-medium text-gray-800">${esc(c.name)}</td>
      <td class="px-4 py-3 text-sm text-gray-600">${esc(c.email)}</td>
      <td class="px-4 py-3 text-sm text-gray-500">${esc(c.phone || '—')}</td>
      <td class="px-4 py-3 text-xs text-gray-400">${fmt(c.created_at)}</td>
    </tr>`).join('') || '<tr><td colspan="4" class="px-4 py-10 text-center text-gray-400">No contacts yet</td></tr>';

  setInner('client-contacts-wrap', `
    <table class="w-full text-left">
      <thead class="text-xs text-gray-500 uppercase bg-gray-50">
        <tr>
          <th class="px-4 py-3">Name</th>
          <th class="px-4 py-3">Email</th>
          <th class="px-4 py-3">Phone</th>
          <th class="px-4 py-3">Added</th>
        </tr>
      </thead>
      <tbody class="divide-y divide-gray-100">${rows}</tbody>
    </table>`);
}

function openAddContactModal() {
  hideError('add-contact-error');
  ['ac-name','ac-email','ac-phone','ac-password'].forEach(id => setVal(id, ''));
  show('add-contact-modal');
}
function closeAddContactModal() { hide('add-contact-modal'); }

async function createClientContact() {
  hideError('add-contact-error');
  if (!currentClientId) return;
  try {
    await apiFetch(`/clients/${currentClientId}/users`, {
      method: 'POST',
      body: JSON.stringify({
        name:     val('ac-name'),
        email:    val('ac-email'),
        phone:    val('ac-phone'),
        password: val('ac-password'),
      }),
    });
    closeAddContactModal();
    openClientDetail(currentClientId);
  } catch(e) { showError('add-contact-error', e.message); }
}

/* ── Job Cards ────────────────────────────────────────────────────────────── */
let editingJobCardId = null;
let jobCardTicketList = [];

async function loadJobCards() {
  const q = val('jc-search');
  const params = new URLSearchParams();
  if (q) params.set('q', q);

  setInner('jc-table-wrap', '<div class="flex justify-center py-12"><div class="spinner"></div></div>');
  try {
    const data = await apiFetch(`/job-cards?${params}`);
    renderJobCardTable(data.job_cards || []);
  } catch(e) { setInner('jc-table-wrap', `<p class="text-red-500 p-4">${e.message}</p>`); }
}

function renderJobCardTable(cards) {
  const rows = cards.map(jc => `
    <tr>
      <td class="px-4 py-3 text-xs font-mono text-tia-700 whitespace-nowrap">${jc.job_card_no}</td>
      <td class="px-4 py-3 text-sm font-medium text-gray-800">${esc(jc.customer_name || '—')}</td>
      <td class="px-4 py-3 text-xs text-gray-500">${jc.ticket_no ? esc(jc.ticket_no) : '—'}</td>
      <td class="px-4 py-3 text-xs text-gray-500">${esc(jc.job_done_by || '—')}</td>
      <td class="px-4 py-3 text-xs text-gray-400 whitespace-nowrap">${fmt(jc.date_received)}</td>
      <td class="px-4 py-3 text-xs text-gray-500">${jc.signed_by ? '<span class="text-green-600">Signed off</span>' : 'Open'}</td>
      <td class="px-4 py-3 text-right">
        <button onclick="openJobCardForm(${jc.id})" class="text-tia-600 hover:underline text-sm mr-3">View / Edit</button>
        ${currentUser.role === 'admin' ? `<button onclick="deleteJobCard(${jc.id})" class="text-red-500 hover:underline text-sm">Delete</button>` : ''}
      </td>
    </tr>`).join('') || '<tr><td colspan="7" class="px-4 py-10 text-center text-gray-400">No job cards found</td></tr>';

  setInner('jc-table-wrap', `
    <table class="w-full text-left">
      <thead class="text-xs text-gray-500 uppercase bg-gray-50">
        <tr>
          <th class="px-4 py-3">Job Card #</th>
          <th class="px-4 py-3">Customer</th>
          <th class="px-4 py-3">Linked Ticket</th>
          <th class="px-4 py-3">Job Done By</th>
          <th class="px-4 py-3">Date Received</th>
          <th class="px-4 py-3">Status</th>
          <th class="px-4 py-3 text-right">Actions</th>
        </tr>
      </thead>
      <tbody class="divide-y divide-gray-100">${rows}</tbody>
    </table>`);
}

async function populateJobCardTicketDropdown(selectedTicketId) {
  const sel = el('jc-ticket-id');
  if (!sel) return;
  try {
    if (jobCardTicketList.length === 0) {
      const data = await apiFetch('/tickets?page=1');
      jobCardTicketList = data.tickets || [];
    }
    sel.innerHTML = '<option value="">— Standalone (no ticket) —</option>' +
      jobCardTicketList.map(t => `<option value="${t.id}">${esc(t.ticket_no)} — ${esc(t.title)}</option>`).join('');
    if (selectedTicketId) sel.value = selectedTicketId;
  } catch(_) {}
}

async function openJobCardForm(id = null) {
  hideError('jc-form-error');
  editingJobCardId = id;
  hide('jc-list-panel');
  show('jc-form-panel');
  hide('jc-print-btn');

  const fields = ['jc-customer-name','jc-address','jc-contact-name','jc-tel-no','jc-email',
    'jc-instruction-taken-by','jc-job-done-by','jc-time-started','jc-time-completed',
    'jc-instructions','jc-comments','jc-signed-by','jc-designation'];
  fields.forEach(f => setVal(f, ''));

  if (id) {
    setText('jc-form-title', 'Loading…');
    try {
      const jc = await apiFetch(`/job-cards/${id}`);
      setText('jc-form-title', jc.job_card_no);
      setVal('jc-customer-name', jc.customer_name);
      setVal('jc-address', jc.address);
      setVal('jc-contact-name', jc.contact_name);
      setVal('jc-tel-no', jc.tel_no);
      setVal('jc-email', jc.email);
      setVal('jc-instruction-taken-by', jc.instruction_taken_by);
      setVal('jc-job-done-by', jc.job_done_by);
      setVal('jc-time-started', jc.time_started);
      setVal('jc-time-completed', jc.time_completed);
      setVal('jc-instructions', jc.instructions);
      setVal('jc-comments', jc.comments);
      setVal('jc-signed-by', jc.signed_by);
      setVal('jc-designation', jc.designation);
      await populateJobCardTicketDropdown(jc.ticket_id);
      show('jc-print-btn');
    } catch(e) { showError('jc-form-error', e.message); }
  } else {
    setText('jc-form-title', 'New Job Card');
    await populateJobCardTicketDropdown(null);
  }
}

function closeJobCardForm() {
  editingJobCardId = null;
  hide('jc-form-panel');
  show('jc-list-panel');
}

async function saveJobCard() {
  hideError('jc-form-error');
  const payload = {
    customer_name:        val('jc-customer-name'),
    address:               val('jc-address'),
    contact_name:          val('jc-contact-name'),
    tel_no:                val('jc-tel-no'),
    email:                 val('jc-email'),
    instruction_taken_by:  val('jc-instruction-taken-by'),
    job_done_by:           val('jc-job-done-by'),
    time_started:          val('jc-time-started'),
    time_completed:        val('jc-time-completed'),
    instructions:          val('jc-instructions'),
    comments:               val('jc-comments'),
    signed_by:             val('jc-signed-by'),
    designation:           val('jc-designation'),
    ticket_id:             val('jc-ticket-id') ? parseInt(val('jc-ticket-id')) : null,
  };
  try {
    if (editingJobCardId) {
      await apiFetch(`/job-cards/${editingJobCardId}`, { method: 'PUT', body: JSON.stringify(payload) });
    } else {
      const created = await apiFetch('/job-cards', { method: 'POST', body: JSON.stringify(payload) });
      editingJobCardId = created.id;
      setText('jc-form-title', created.job_card_no);
      show('jc-print-btn');
    }
    loadJobCards();
  } catch(e) { showError('jc-form-error', e.message); }
}

async function deleteJobCard(id) {
  if (!confirm('Delete this job card? This cannot be undone.')) return;
  try {
    await apiFetch(`/job-cards/${id}`, { method: 'DELETE' });
    loadJobCards();
  } catch(e) { alert(e.message); }
}

async function printJobCard() {
  if (!editingJobCardId) return;
  let jc;
  try {
    jc = await apiFetch(`/job-cards/${editingJobCardId}`);
  } catch(e) { return alert('Could not load job card for printing: ' + e.message); }

  const tpl = el('jc-print-template');
  if (!tpl) return;

  const setTxt = (id, v) => { const e2 = tpl.querySelector('#' + id); if (e2) e2.textContent = v || ''; };
  setTxt('jcp-jobno2', jc.job_card_no);
  setTxt('jcp-customer', jc.customer_name);
  setTxt('jcp-address', jc.address);
  setTxt('jcp-contact', jc.contact_name);
  setTxt('jcp-tel', jc.tel_no);
  setTxt('jcp-email', jc.email);
  setTxt('jcp-date-received', jc.date_received ? fmt(jc.date_received) : '');
  setTxt('jcp-taken-by', jc.instruction_taken_by);
  setTxt('jcp-done-by', jc.job_done_by);
  setTxt('jcp-time-started', jc.time_started);
  setTxt('jcp-time-completed', jc.time_completed);
  setTxt('jcp-instructions', jc.instructions);
  setTxt('jcp-comments', jc.comments);
  setTxt('jcp-signed-by', jc.signed_by);
  setTxt('jcp-designation', jc.designation);
  setTxt('jcp-signed-date', jc.signed_date ? fmt(jc.signed_date) : '');

  const printWindow = window.open('', '_blank', 'width=850,height=1000');
  if (!printWindow) return alert('Please allow pop-ups to print the job card.');
  printWindow.document.write(`<!DOCTYPE html><html><head><title>${jc.job_card_no}</title></head><body>${tpl.innerHTML}</body></html>`);
  printWindow.document.close();

  const triggerPrint = () => { printWindow.focus(); printWindow.print(); };
  const bgImg = printWindow.document.querySelector('.jc-print-bg');
  if (bgImg && !bgImg.complete) {
    bgImg.addEventListener('load', triggerPrint, { once: true });
    bgImg.addEventListener('error', triggerPrint, { once: true }); // print anyway if the image fails, rather than never printing
    setTimeout(triggerPrint, 3000); // fallback in case neither event fires
  } else {
    printWindow.onload = triggerPrint;
  }
}

/* ── Init ─────────────────────────────────────────────────────────────────── */
(async function init() {
  // A password-reset email link takes priority over any existing session —
  // show the reset form directly rather than booting into the app.
  const params = new URLSearchParams(window.location.search);
  const tokenFromLink = params.get('token');
  if (window.location.pathname.replace(/\/$/, '').endsWith('reset-password') && tokenFromLink) {
    resetPasswordToken = tokenFromLink;
    show('auth-screen');
    hideAllAuthForms();
    show('reset-password-form');
    return;
  }

  const saved = token();
  if (!saved) { show('auth-screen'); showLogin(); return; }
  try {
    currentUser = await apiFetch('/auth/me');
    bootApp();
  } catch(_) {
    localStorage.removeItem('tia_token');
    show('auth-screen');
    showLogin();
  }
})();
