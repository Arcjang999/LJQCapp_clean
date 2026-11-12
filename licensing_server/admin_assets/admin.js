'use strict';

(() => {
  const $ = id => document.getElementById(id);
  const state = {csrf: '', authenticated: false, licenses: [], selected: null, issuedCode: '', confirm: null,
    epoch: 0, idleUntil: 0, idleWindow: 30 * 60 * 1000};
  const pendingRequests = new Set();
  const beijing = new Intl.DateTimeFormat('zh-CN', {timeZone: 'Asia/Shanghai', year: 'numeric',
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23'});
  const dateParts = new Intl.DateTimeFormat('en-CA', {timeZone: 'Asia/Shanghai', year: 'numeric',
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23'});
  let idleTimer;
  let absoluteTimer;

  function text(id, value) { $(id).textContent = value; }
  function feedback(id, message = '', type = 'error') {
    const element = $(id);
    element.textContent = message;
    element.className = 'feedback ' + type;
    element.hidden = !message;
  }
  function time(value) { return Number.isFinite(value) ? beijing.format(new Date(value * 1000)) : '未记录'; }
  function status(row) { return !row.enabled ? 'disabled' : row.expires_at <= Date.now() / 1000 ? 'expired' : 'active'; }
  function badge(value, device = false) {
    const element = document.createElement('span');
    element.className = 'badge' + (value === 'active' ? '' : ' ' + value);
    element.textContent = device ? (value === 'active' ? '已激活' : '已解绑') :
      ({active: '使用中', disabled: '已停用', expired: '已到期'}[value]);
    return element;
  }
  function cell(value, className = '') {
    const element = document.createElement('td');
    element.textContent = value;
    if (className) element.className = className;
    return element;
  }
  function rowButton(label, handler, className = 'row-action') {
    const button = document.createElement('button');
    button.type = 'button'; button.className = className; button.textContent = label;
    button.addEventListener('click', handler);
    return button;
  }
  function closeDialog(id) { if ($(id).open) $(id).close(); }
  function clearIssued() { state.issuedCode = ''; $('issued-code').value = ''; text('issued-summary', ''); feedback('copy-message'); }
  function lock(message = '') {
    state.epoch += 1;
    pendingRequests.forEach(controller => controller.abort());
    clearTimeout(idleTimer); clearTimeout(absoluteTimer);
    document.querySelectorAll('dialog[open]').forEach(dialog => dialog.close());
    state.authenticated = false; state.csrf = ''; state.licenses = []; state.selected = null;
    clearIssued(); state.confirm = null;
    $('license-rows').replaceChildren(); $('device-rows').replaceChildren();
    text('detail-title', '验证码详情'); text('detail-id', ''); text('current-user', '');
    $('admin-screen').hidden = true; $('login-screen').hidden = false;
    $('password').value = '';
    feedback('login-error', message);
  }
  function resetIdleTimer() {
    if (!state.authenticated) return;
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => expireLocally(), Math.max(0, Math.min(30 * 60 * 1000, state.idleUntil - Date.now())));
  }
  function expireLocally() {
    // Clear displayed codes immediately, then invalidate the server session.
    const csrf = state.csrf;
    lock('登录已过期，请重新登录。');
    fetch('/api/logout', {method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, body: '{}'}).catch(() => {});
  }
  async function api(path, payload) {
    const epoch = state.epoch;
    const controller = new AbortController();
    pendingRequests.add(controller);
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
      const options = {method: payload === undefined ? 'GET' : 'POST', credentials: 'same-origin',
        cache: 'no-store', signal: controller.signal, headers: {'Accept': 'application/json'}};
      if (payload !== undefined) {
        options.headers['Content-Type'] = 'application/json';
        options.headers['X-CSRF-Token'] = state.csrf;
        options.body = JSON.stringify(payload);
      }
      const response = await fetch(path, options);
      const result = await response.json();
      if (epoch !== state.epoch) throw new Error('会话已变化，请重新登录后查看。');
      if (response.status === 401 && state.authenticated) lock('登录已过期，请重新登录。');
      if (!response.ok) throw new Error(typeof result.message === 'string' ? result.message.slice(0, 240) : '操作未完成，请重试。');
      if (state.authenticated) { state.idleUntil = Date.now() + state.idleWindow; resetIdleTimer(); }
      return result;
    } catch (error) {
      if (epoch !== state.epoch) throw new Error('会话已变化，请重新登录后查看。');
      if (error.name === 'AbortError' || error instanceof TypeError)
        throw new Error(payload === undefined ? '暂时无法连接管理服务，请重试。' : '未收到操作结果，请刷新列表核对后再试。');
      throw error;
    } finally { clearTimeout(timer); pendingRequests.delete(controller); }
  }
  async function busy(button, pending, action) {
    const label = button.textContent;
    if (button.disabled) return;
    button.disabled = true; button.textContent = pending;
    try { await action(); } finally { button.disabled = false; button.textContent = label; }
  }
  function setAuthenticated(session) {
    state.epoch += 1;
    pendingRequests.forEach(controller => controller.abort());
    state.csrf = session.csrf; state.authenticated = true;
    $('login-screen').hidden = true; $('admin-screen').hidden = false;
    text('current-user', session.username || '管理员');
    feedback('login-error'); $('password').value = '';
    clearTimeout(absoluteTimer);
    const now = Date.now();
    const absoluteRemaining = Number.isFinite(session.expires_at) ? session.expires_at * 1000 - now : 60 * 60 * 1000;
    const idleRemaining = Number.isFinite(session.idle_expires_at) ? session.idle_expires_at * 1000 - now : 30 * 60 * 1000;
    state.idleWindow = Math.min(30 * 60 * 1000, Math.max(0, idleRemaining));
    state.idleUntil = now + state.idleWindow;
    absoluteTimer = setTimeout(() => expireLocally(), Math.max(0, Math.min(60 * 60 * 1000, absoluteRemaining)));
    resetIdleTimer();
  }
  async function bootstrap() {
    try {
      const session = await api('/api/session');
      state.csrf = session.csrf;
      if (session.authenticated) { setAuthenticated(session); await loadLicenses(); }
    } catch (error) { feedback('login-error', error.message); }
  }
  async function login() {
    const form = $('login-form');
    if (!form.reportValidity()) return;
    feedback('login-error');
    await busy($('login-button'), '登录中…', async () => {
      try {
        if (!state.csrf) { const session = await api('/api/session'); state.csrf = session.csrf; }
        const session = await api('/api/login', {username: $('username').value.trim(), password: $('password').value});
        setAuthenticated(session); await loadLicenses();
      } catch (error) { feedback('login-error', error.message); }
    });
  }
  function renderLicenses() {
    const rows = state.licenses;
    text('stat-total', rows.length);
    text('stat-active', rows.filter(row => status(row) === 'active').length);
    text('stat-devices', rows.reduce((total, row) => total + row.active_devices, 0));
    text('stat-inactive', rows.filter(row => status(row) !== 'active').length);
    const query = $('search').value.trim().toLocaleLowerCase();
    const filter = $('status-filter').value;
    const visible = rows.filter(row => (!query || (row.label + ' ' + row.id).toLocaleLowerCase().includes(query)) &&
      (filter === 'all' || status(row) === filter));
    const fragment = document.createDocumentFragment();
    visible.forEach(row => {
      const tr = document.createElement('tr');
      const label = cell('');
      const name = document.createElement('span'); name.className = 'license-label'; name.textContent = row.label || '未填写备注';
      const id = document.createElement('span'); id.className = 'record-id'; id.textContent = row.id.slice(0, 12); id.title = row.id;
      label.append(name, id);
      const statusCell = cell(''); statusCell.append(badge(status(row)));
      const seats = cell('', 'seat-count'); const total = document.createElement('span'); total.textContent = ' / ' + row.max_devices;
      seats.append(document.createTextNode(String(row.active_devices)), total);
      const actions = cell(''); actions.append(rowButton('查看与管理', () => openDetail(row.id)));
      tr.append(label, statusCell, seats, cell(time(row.expires_at)), cell(time(row.created_at)), actions);
      fragment.append(tr);
    });
    $('license-rows').replaceChildren(fragment);
    $('empty-state').hidden = visible.length !== 0;
    text('empty-title', rows.length ? '没有匹配的记录' : '尚未签发验证码');
    text('empty-text', rows.length ? '调整备注搜索或状态筛选后重试。' : '点击“生成验证码”，设置允许台数和截止时间。');
    text('record-count', visible.length === rows.length ? rows.length + ' 条记录' : '显示 ' + visible.length + ' / ' + rows.length + ' 条记录');
  }
  async function loadLicenses() {
    const result = await api('/api/licenses');
    state.licenses = result.licenses.slice().reverse(); renderLicenses();
  }
  function deadline(prefix) {
    if (!$(prefix + '-date').value || !$(prefix + '-time').value) throw new Error('请选择截止日期和时间。');
    const value = $(prefix + '-date').value + 'T' + $(prefix + '-time').value + ':00+08:00';
    if (!Number.isFinite(Date.parse(value)) || Date.parse(value) <= Date.now()) throw new Error('截止时间须晚于当前时间。');
    return value;
  }
  function fillDeadline(prefix, epoch) {
    const parts = Object.fromEntries(dateParts.formatToParts(new Date(epoch * 1000)).map(part => [part.type, part.value]));
    $(prefix + '-date').value = parts.year + '-' + parts.month + '-' + parts.day;
    $(prefix + '-time').value = parts.hour + ':' + parts.minute;
  }
  async function issue() {
    if (!$('issue-form').reportValidity()) return;
    feedback('issue-error');
    await busy($('issue-submit'), '生成中…', async () => {
      try {
        const devices = Number($('issue-devices').value);
        if (!Number.isInteger(devices) || devices < 1 || devices > 1000) throw new Error('允许台数须为1到1000之间的整数。');
        const result = await api('/api/licenses', {label: $('issue-label').value.trim(), max_devices: devices, expires_at: deadline('issue')});
        closeDialog('issue-dialog');
        state.issuedCode = result.code; $('issued-code').value = result.code;
        text('issued-summary', '最多 ' + result.max_devices + ' 台电脑 · 到期 ' + time(result.expires_at) + '（北京时间）');
        feedback('copy-message'); $('issued-dialog').showModal();
        $('issue-label').value = '';
        await loadLicenses();
      } catch (error) {
        feedback($('issue-dialog').open ? 'issue-error' : 'page-message', error.message);
      }
    });
  }
  async function copyCode() {
    if (!state.issuedCode) return;
    try { await navigator.clipboard.writeText(state.issuedCode); feedback('copy-message', '验证码已复制，请保存后发送给客户。', 'success'); }
    catch (_) { $('issued-code').focus(); $('issued-code').select(); feedback('copy-message', '请复制已选中的验证码。', 'success'); }
  }
  function renderDetail(license, devices, preserveDate = false) {
    state.selected = license;
    text('detail-title', license.label || '验证码详情'); text('detail-id', '记录编号：' + license.id);
    $('detail-status').replaceChildren(badge(status(license))); text('detail-seats', license.active_devices + ' / ' + license.max_devices);
    text('detail-expires', time(license.expires_at));
    if (!preserveDate) fillDeadline('extend', license.expires_at);
    const minimum = Object.fromEntries(dateParts.formatToParts(new Date(license.expires_at * 1000)).map(part => [part.type, part.value]));
    $('extend-date').min = minimum.year + '-' + minimum.month + '-' + minimum.day;
    const toggle = $('toggle-license'); toggle.textContent = license.enabled ? '停用验证码' : '重新启用';
    toggle.className = license.enabled ? 'danger-outline' : 'secondary';
    const fragment = document.createDocumentFragment();
    devices.forEach(device => {
      const tr = document.createElement('tr');
      const identity = cell('设备 ' + device.id.slice(0, 8)); identity.title = device.id;
      const statusCell = cell(''); statusCell.append(badge(device.active ? 'active' : 'disabled', true));
      const first = cell(device.first_ip || '未记录', device.first_ip ? 'ip-value' : 'muted-value');
      const last = cell(device.last_ip || '未记录', device.last_ip ? 'ip-value' : 'muted-value');
      const actions = cell('');
      if (device.active) actions.append(rowButton('解绑', () => confirmUnbind(license, device), 'danger-outline'));
      else actions.textContent = '—';
      tr.append(identity, statusCell, cell(time(device.created_at)), cell(time(device.last_seen_at)), first, last, actions);
      fragment.append(tr);
    });
    $('device-rows').replaceChildren(fragment); $('no-devices').hidden = devices.length !== 0;
  }
  async function loadDetail(id, preserveDate = false) {
    const result = await api('/api/licenses/' + encodeURIComponent(id) + '/devices');
    renderDetail(result.license, result.devices, preserveDate);
  }
  async function openDetail(id) {
    feedback('page-message');
    try { await loadDetail(id); feedback('detail-message'); $('detail-dialog').showModal(); }
    catch (error) { feedback('page-message', error.message); }
  }
  async function refreshDetail() {
    if (!state.selected) return;
    const id = state.selected.id;
    await loadLicenses(); await loadDetail(id, true);
  }
  async function extend() {
    if (!state.selected || !$('extend-form').reportValidity()) return;
    feedback('detail-message');
    await busy($('extend-button'), '保存中…', async () => {
      try {
        const until = deadline('extend');
        if (Date.parse(until) / 1000 <= state.selected.expires_at) throw new Error('请选择晚于原截止时间的新期限。');
        await api('/api/licenses/' + state.selected.id + '/extend', {expires_at: until});
        await refreshDetail(); feedback('detail-message', '使用期限已延长，电脑下次联网验证时自动更新。', 'success');
      } catch (error) { feedback('detail-message', error.message); }
    });
  }
  function confirmAction(title, message, label, action) {
    text('confirm-title', title); text('confirm-text', message); text('confirm-submit', label);
    feedback('confirm-error'); state.confirm = action; $('confirm-dialog').showModal();
  }
  function confirmToggle() {
    const license = state.selected;
    if (!license) return;
    const disabling = Boolean(license.enabled);
    confirmAction(disabling ? '停用这个验证码？' : '重新启用这个验证码？', disabling ?
      '已联网电脑下次验证时停止使用。临时断网的电脑最多仍可使用至原24小时期限。' :
      '重新启用后，原电脑需要再次输入验证码联网验证，已用电脑名额保留。', disabling ? '确认停用' : '确认启用', async () => {
        await api('/api/licenses/' + license.id + (disabling ? '/disable' : '/enable'), {});
        await refreshDetail(); feedback('detail-message', disabling ? '验证码已停用。' : '验证码已重新启用。', 'success');
      });
  }
  function confirmUnbind(license, device) {
    confirmAction('解绑这台电脑？', '解绑设备 ' + device.id.slice(0, 8) + ' 将释放一个电脑名额并撤销它当前的激活。临时断网电脑最多仍可使用至原24小时期限。', '确认解绑', async () => {
      await api('/api/licenses/' + license.id + '/devices/' + device.id + '/unbind', {});
      await refreshDetail(); feedback('detail-message', '电脑已解绑，已释放一个名额。', 'success');
    });
  }
  async function submitConfirm() {
    if (!state.confirm) return;
    const action = state.confirm;
    await busy($('confirm-submit'), '处理中…', async () => {
      try { await action(); closeDialog('confirm-dialog'); }
      catch (error) { feedback('confirm-error', error.message); }
    });
  }

  // Every change is committed only by its named button, including date/time forms.
  document.querySelectorAll('form').forEach(form => form.addEventListener('submit', event => event.preventDefault()));
  $('login-button').addEventListener('click', login);
  $('logout-button').addEventListener('click', async () => {
    await busy($('logout-button'), '退出中…', async () => {
      try { await api('/api/logout', {}); lock(); await bootstrap(); }
      catch (error) { feedback('page-message', error.message); }
    });
  });
  $('new-code-button').addEventListener('click', () => { feedback('issue-error'); $('issue-dialog').showModal(); });
  $('issue-submit').addEventListener('click', issue);
  $('copy-code-button').addEventListener('click', copyCode);
  $('refresh-button').addEventListener('click', () => busy($('refresh-button'), '刷新中…', async () => {
    try { await loadLicenses(); feedback('page-message'); } catch (error) { feedback('page-message', error.message); }
  }));
  $('refresh-devices').addEventListener('click', () => busy($('refresh-devices'), '刷新中…', async () => {
    try { await refreshDetail(); feedback('detail-message'); } catch (error) { feedback('detail-message', error.message); }
  }));
  $('search').addEventListener('input', renderLicenses);
  $('status-filter').addEventListener('change', renderLicenses);
  $('extend-button').addEventListener('click', extend);
  $('toggle-license').addEventListener('click', confirmToggle);
  $('confirm-submit').addEventListener('click', submitConfirm);
  document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => closeDialog(button.dataset.close)));
  $('issued-dialog').addEventListener('close', clearIssued);
  $('confirm-dialog').addEventListener('close', () => { state.confirm = null; });
  ['pointerdown', 'keydown'].forEach(event => document.addEventListener(event, resetIdleTimer, {passive: true}));
  window.addEventListener('pageshow', event => { if (event.persisted) { lock(); bootstrap(); } });
  bootstrap();
})();
