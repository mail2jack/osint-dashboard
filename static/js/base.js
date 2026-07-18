// CSRF-safe fetch wrapper — auto-adds X-CSRFToken header
window.apiFetch = function(url, options) {
  options = options || {};
  options.headers = options.headers || {};
  var token = window.csrfToken || (document.querySelector('meta[name="csrf-token"]') || {}).content;
  if (token) {
    options.headers['X-CSRFToken'] = token;
  }
  return fetch(url, options);
};

(function() {
  var C = window.CMS || {};
  if (!C.isAdmin) return;
  document.addEventListener('DOMContentLoaded', function() {
    fetch('/cms/api/check-update', { headers: { 'Accept': 'application/json' } })
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (!data.update_available) return;
        var banner = document.getElementById('updateBanner');
        var text = document.getElementById('updateBannerText');
        if (data.version_update) {
          text.textContent = 'Update ' + data.current_version + ' \u2192 ' + data.latest_version + ' available. Click to update.';
          document.getElementById('updCurrent').textContent = data.current_version;
          document.getElementById('updLatest').textContent = data.latest_version;
        } else {
          text.textContent = 'New commits available (bugfixes/improvements). Click to update.';
          document.getElementById('updCurrent').textContent = data.current_version;
          document.getElementById('updLatest').textContent = data.current_version + ' (+commits)';
        }
        if (data.changelog) {
          var clEl = document.getElementById('updateChangelog');
          var lines = data.changelog.split('\n').filter(Boolean);
          var clHtml = lines.map(function(l) {
            if (l.startsWith('### ')) return '<b>' + esc(l.slice(4)) + '</b>';
            if (l.startsWith('- ')) return '\u2022 ' + esc(l.slice(2));
            if (l.startsWith('## ')) return '<b style="font-size:1.1rem;">' + esc(l.replace(/[\[\]]/g,'')) + '</b>';
            return esc(l);
          }).join('<br>');
          clEl.innerHTML = '<strong>\U0001f4cb What\'s new:</strong><div style="margin-top:0.5rem;">' + clHtml + '</div>';
          clEl.style.display = 'block';
        }
        document.getElementById('updateReview').style.display = 'block';
        banner.style.display = 'block';
      })
      .catch(function() {});
  });
})();

(function() {
  var C = window.CMS || {};
  if (C.sfHealth && C.sfHealth !== 'ok') {
    var el = document.getElementById('sfHealthBanner');
    if (el) el.style.display = 'block';
    var text = document.getElementById('sfHealthText');
    if (text) text.textContent = C.sfHealth === 'error' ? 'unreachable' : C.sfHealth;
  }
  if (C.loginAnomalyCount > 0) {
    var cnt = document.getElementById('loginAnomalyCount');
    if (cnt) cnt.textContent = C.loginAnomalyCount;
    var ban = document.getElementById('loginAnomalyBanner');
    if (ban) ban.style.display = 'block';
  }
})();

(function() {
  var LIFETIME = (window.CMS || {}).sessionLifetime || 28800;
  var WARN_BEFORE = 300;
  var CHECK_INTERVAL = 60000;
  var warned = false;

  function getAge() {
    var a = sessionStorage.getItem('session_start');
    if (!a) { a = Date.now().toString(); sessionStorage.setItem('session_start', a); }
    return (Date.now() - parseInt(a)) / 1000;
  }
  function remaining() { return LIFETIME - getAge(); }
  function showWarn() {
    if (warned) return;
    warned = true;
    var m = document.getElementById('sessionTimeoutModal');
    if (m) m.style.display = 'flex';
  }
  function updateCountdown() {
    var rem = remaining();
    var el = document.getElementById('sessionTimeoutCountdown');
    if (el) {
      var m = Math.floor(rem / 60);
      var s = Math.floor(rem % 60);
      el.textContent = m + ':' + (s < 10 ? '0' : '') + s;
    }
  }
  setInterval(function() {
    var rem = remaining();
    if (rem <= 0) {
      // Session expired — try to extend silently instead of hard-reloading
      apiFetch('/api/keep-alive', { method: 'POST' })
        .then(function(r) { return r.json(); })
        .then(function(d) {
          if (d.status === 'ok') {
            sessionStorage.setItem('session_start', Date.now().toString());
            warned = false;
          }
        })
        .catch(function() {});
    } else if (rem <= WARN_BEFORE && !warned) {
      showWarn();
      updateCountdown();
      setInterval(updateCountdown, 1000);
    }
  }, CHECK_INTERVAL);

  window.extendSession = function() {
    apiFetch('/api/keep-alive', { method: 'POST' })
      .then(function(r) { return r.json(); })
      .then(function(d) {
        if (d.status === 'ok') {
          sessionStorage.setItem('session_start', Date.now().toString());
          warned = false;
          var m = document.getElementById('sessionTimeoutModal');
          if (m) m.style.display = 'none';
        }
      })
      .catch(function() {});
  };
})();

(function() {
  var C = window.CMS || {};
  var badge = document.getElementById('notificationBadge');
  var panel = document.getElementById('notificationPanel');
  if (C.notificationCount > 0 && badge) {
    badge.style.display = 'block';
    badge.textContent = C.notificationCount > 99 ? '99+' : C.notificationCount;
  }

  window.toggleNotificationPanel = function() {
    if (!panel) return;
    if (panel.style.display === 'block') { panel.style.display = 'none'; return; }
    fetch(C.notificationListUrl + '?limit=10')
      .then(function(r) { return r.json(); })
      .then(function(data) {
        var h = '<div style="padding:0.75rem 1rem;border-bottom:1px solid var(--border-color);font-weight:600;display:flex;justify-content:space-between;align-items:center;"><span>Notifications</span>';
        if (data.unread_count > 0) h += '<button class="btn btn-xs" data-click="markAllRead">Mark all read</button>';
        h += '<a href="/cms/notifications" style="font-size:0.7rem;color:var(--accent);text-decoration:none;font-weight:400;">See all</a>';
        h += '</div>';
        if (data.notifications.length === 0) {
          h += '<div style="padding:1.5rem;text-align:center;color:var(--text-secondary);font-size:0.875rem;">' + (C.noNotificationsText || 'No notifications') + '</div>';
        } else {
          data.notifications.forEach(function(n) {
            var safeId = String(n.id).replace(/"/g, '&quot;');
            var safeLink = String(n.link || '').replace(/"/g, '&quot;');
            h += '<div class="notif-item' + (n.is_read ? '' : ' unread') + '" data-click="clickNotificationBtn" data-arg0="' + safeId + '" data-arg1="' + safeLink + '">';
            h += '<div>' + esc(n.message) + '</div>';
            h += '<div class="notif-time">' + new Date(n.created_at).toLocaleString() + '</div></div>';
          });
        }
        panel.innerHTML = h;
        panel.style.display = 'block';
      });
  };

  window.clickNotification = function(id, link) {
    var url = C.notificationReadUrl.replace('__ID__', id);
    apiFetch(url, { method: 'POST' })
      .then(function() {
        if (panel) panel.style.display = 'none';
        if (link) window.location.href = link;
      });
  };
  window.clickNotificationBtn = function(btn) {
    clickNotification(btn.dataset.arg0, btn.dataset.arg1 || '');
  };

  window.markAllRead = function() {
    apiFetch(C.notificationReadAllUrl, { method: 'POST' })
      .then(function() {
        if (badge) badge.style.display = 'none';
        if (panel) panel.style.display = 'none';
      });
  };

  document.addEventListener('click', function(e) {
    var bell = document.getElementById('notificationBell');
    if (panel && panel.style.display === 'block' && !panel.contains(e.target) && !bell.contains(e.target)) {
      panel.style.display = 'none';
    }
  });
})();

setInterval(function() {
  fetch('/health?quick=1', { headers: { 'Accept': 'application/json' } })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      var banner = document.getElementById('sfHealthBanner');
      if (!banner) return;
      if (d.spiderfoot === 'ok') {
        banner.style.display = 'none';
      } else if (d.spiderfoot && d.spiderfoot !== 'ok' && d.spiderfoot !== 'not configured') {
        banner.style.display = 'block';
        document.getElementById('sfHealthText').textContent = 'unreachable';
      }
    })
    .catch(function() {});
}, 60000);

function toggleTheme() {
  var html = document.documentElement;
  var next = html.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
  html.setAttribute('data-theme', next);
  localStorage.setItem('cms-theme', next);
  var icon = document.getElementById('theme-icon');
  if (icon) icon.textContent = next === 'dark' ? '☀️' : '🌙';
}

document.addEventListener('DOMContentLoaded', function() {
  var theme = localStorage.getItem('cms-theme') || 'light';
  var icon = document.getElementById('theme-icon');
  if (icon) icon.textContent = theme === 'dark' ? '☀️' : '🌙';

  fetch('/api/version')
    .then(function(r) { return r.json(); })
    .then(function(d) {
      var el = document.getElementById('footerVersion');
      if (el) el.textContent = d.version;
    })
    .catch(function() {});
});

function openChangelog() {
  var modal = document.getElementById('changelogModal');
  var content = document.getElementById('changelogContent');
  if (modal) modal.classList.add('show');
  if (content) content.innerHTML = '<div style="text-align:center;padding:2rem;color:var(--text-secondary);">Loading...</div>';
  fetch('/api/changelog')
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (content) content.innerHTML = d.html || '<div style="text-align:center;padding:2rem;">No changelog available</div>';
    })
    .catch(function() {
      if (content) content.innerHTML = '<div style="text-align:center;padding:2rem;color:#dc2626;">Failed to load changelog</div>';
    });
}

document.addEventListener('keydown', function(e) {
  if (e.target.matches('input, textarea, select')) return;

  if (e.key === '/' && !e.ctrlKey && !e.metaKey) {
    e.preventDefault();
    var input = document.querySelector('input[name="search"]') || document.querySelector('#search-input');
    if (input) input.focus();
  }

  if (e.key === 'j' || e.key === 'k') {
    var selected = document.querySelector('tr.selected');
    var rows = Array.from(document.querySelectorAll('tbody tr:not(.select-all-row)'));
    if (!rows.length) return;
    var idx = rows.indexOf(selected);
    var next;
    if (e.key === 'j') next = rows[idx + 1] || rows[0];
    else next = rows[idx - 1] || rows[rows.length - 1];
    if (selected) selected.classList.remove('selected');
    if (next) { next.classList.add('selected'); next.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }
  }

  if (e.key === 'Enter') {
    var sel = document.querySelector('tr.selected');
    if (sel) {
      var link = sel.querySelector('a[href]');
      if (link) window.location.href = link.href;
    }
  }

  if (e.key === '?') { e.preventDefault(); openHelp(); }
});

(function() {
  var s = document.createElement('style');
  s.textContent = 'tbody tr.selected { background: var(--table-hover) !important; }';
  document.head.appendChild(s);
})();

function hideLoader() {
  var loader = document.getElementById('appLoader');
  if (loader) {
    setTimeout(function() {
      loader.classList.add('hidden');
      setTimeout(function() { if (loader.parentNode) loader.parentNode.removeChild(loader); }, 300);
    }, 100);
  }
}

if ('serviceWorker' in navigator) {
  window.addEventListener('load', function() {
    navigator.serviceWorker.register('/static/pwa/sw.js')
      .then(function(reg) { console.log('SW registered'); hideLoader(); })
      .catch(function(err) { console.log('SW registration failed:', err); hideLoader(); });
  });
} else {
  hideLoader();
}

document.addEventListener('DOMContentLoaded', function() {
  setTimeout(hideLoader, 2000);
});

function showUpdateModal() { var m = document.getElementById('updateModal'); if (m) m.classList.add('show'); }
function closeParentModal(btn) { var m = btn.closest('.modal'); if (m) m.classList.remove('show'); }
function closeChangelogModal() { var m = document.getElementById('changelogModal'); if (m) m.classList.remove('show'); }
function submitParentForm(el) { if (el.form) el.form.submit(); }
function removeEntry(btn, selector) { var el = btn.closest(selector); if (el) el.parentNode.removeChild(el); }
function navigateTo(btn, url) { window.location.href = url; }
function reloadPage(btn) { location.reload(); }
function closeHelp() {
  var p = document.getElementById('helpPanel');
  var o = document.getElementById('helpOverlay');
  if (p) p.classList.remove('open');
  if (o) { o.classList.remove('open'); o.style.display = 'none'; }
}
function esc(str) {
  var d = document.createElement('div');
  d.appendChild(document.createTextNode(str != null ? String(str) : ''));
  return d.innerHTML;
}
function openHelp(topic) {
  if (typeof topic !== 'string' || !topic) topic = document.body.dataset.helpTopic || 'general';
  var title = document.getElementById('helpPanelTitle');
  if (title) title.textContent = 'Help: ' + topic.charAt(0).toUpperCase() + topic.slice(1);
  var content = document.getElementById('helpPanelContent');
  if (content) content.innerHTML = '<p style="color:var(--text-secondary);">Loading...</p>';
  var panel = document.getElementById('helpPanel');
  if (panel) panel.classList.add('open');
  var overlay = document.getElementById('helpOverlay');
  if (overlay) overlay.style.display = 'block';
  fetch('/cms/api/help/' + encodeURIComponent(topic))
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (content) content.innerHTML = data.html;
    })
    .catch(function() {
      if (content) content.innerHTML = '<p style="color:var(--error);">Failed to load help content.</p>';
    });
}

// Toast notification system
function showToast(message, type, duration) {
  type = type || 'info';
  duration = duration || 4000;
  var container = document.getElementById('toast-container');
  if (!container) return;
  var toast = document.createElement('div');
  toast.className = 'toast toast-' + type;
  var icons = { success: '\u2705', error: '\u274c', warning: '\u26a0\ufe0f', info: '\u2139\ufe0f' };
  toast.innerHTML = '<span>' + (icons[type] || '\u2139\ufe0f') + '</span><span>' + esc(message) + '</span>';
  toast.addEventListener('click', function() { if (toast.parentNode) toast.parentNode.removeChild(toast); });
  container.appendChild(toast);
  setTimeout(function() {
    toast.style.opacity = '0';
    toast.style.transition = 'opacity 0.3s';
    setTimeout(function() { if (toast.parentNode) toast.parentNode.removeChild(toast); }, 300);
  }, duration);
}
function showLoading(text) {
  var el = document.getElementById('actionLoaderText');
  if (el) el.textContent = text || 'Loading...';
  var l = document.getElementById('actionLoader');
  if (l) l.classList.add('show');
}
function hideLoading() {
  var l = document.getElementById('actionLoader');
  if (l) l.classList.remove('show');
}
function handleApiResponse(response, toastOnError) {
  if (toastOnError === undefined) toastOnError = true;
  if (!response.ok) {
    return response.json().then(function(data) {
      var msg = data.error || data.message || 'Request failed (' + response.status + ')';
      if (toastOnError) showToast(msg, 'error');
      return Promise.reject({ status: response.status, data: data, message: msg });
    }).catch(function(err) {
      if (err && err.status) return Promise.reject(err);
      var msg = 'Request failed (' + response.status + ')';
      if (toastOnError) showToast(msg, 'error');
      return Promise.reject({ status: response.status, message: msg });
    });
  }
  return response.json().catch(function() { return {}; });
}

// Place cursor at end of search inputs
document.addEventListener('DOMContentLoaded', function() {
  var inputs = document.querySelectorAll('[data-immediate-search]');
  for (var i = 0; i < inputs.length; i++) {
    if (inputs[i].value.length) {
      inputs[i].focus();
      inputs[i].setSelectionRange(inputs[i].value.length, inputs[i].value.length);
    }
  }
});

// Global event delegation system
document.addEventListener('click', function(e) {
  var el = e.target.closest('[data-click]');
  if (!el) return;
  var fn = window[el.dataset.click];
  if (typeof fn === 'function') {
    var args = [];
    for (var i = 0; ; i++) {
      var key = 'arg' + i;
      if (el.dataset[key] !== undefined) {
        try { args.push(JSON.parse(el.dataset[key])); }
        catch(e) { args.push(el.dataset[key]); }
      } else break;
    }
    fn(el, ...args);
    if (!el.dataset.clickNoStop) e.preventDefault();
  }
});
document.addEventListener('change', function(e) {
  var el = e.target.closest('[data-change]');
  if (!el) return;
  var fn = window[el.dataset.change];
  if (typeof fn === 'function') fn(el);
});
document.addEventListener('submit', function(e) {
  var el = e.target.closest('[data-submit]');
  if (!el) return;
  e.preventDefault();
  // For POST forms: only trigger on explicit submit button click, not Enter key
  if (el.method && el.method.toLowerCase() === 'post' && (!e.submitter || e.submitter.type !== 'submit')) return;
  var fn = window[el.dataset.submit];
  if (typeof fn === 'function') fn(e);
});
document.addEventListener('input', function(e) {
  var el = e.target.closest('[data-input]');
  if (!el) return;
  var fn = window[el.dataset.input];
  if (typeof fn === 'function') fn(el);
});
document.addEventListener('input', function(e) {
  var el = e.target.closest('[data-immediate-search]');
  if (!el) return;
  var val = el.value.trim();
  clearTimeout(el._searchTimer);
  if (val.length >= 2) {
    el._searchTimer = setTimeout(function() {
      var form = el.closest('form');
      if (form) form.submit();
    }, 250);
  }
});

// Update runner (admin-only, called from data-click)
var _updatePollTimer = null;
var _updateTaskId = null;

function _updateUI(mode) {
  var title = document.getElementById('updateModalTitle');
  var review = document.getElementById('updateReview');
  var confirm = document.getElementById('updateConfirm');
  var progress = document.getElementById('updateProgress');
  var result = document.getElementById('updateResult');
  var btn = document.getElementById('updateNowBtn');
  var laterBtn = document.getElementById('updateLaterBtn');
  var backBtn = document.getElementById('updateBackBtn');
  var reviewLaterBtn = document.getElementById('updateReviewLaterBtn');
  var reviewConfirmBtn = document.getElementById('updateReviewConfirmBtn');
  var abortBtn = document.getElementById('updateAbortBtn');
  var rollbackBtn = document.getElementById('updateRollbackBtn');
  var footer = document.getElementById('updateFooterText');

  function hideAll() {
    if (review) review.style.display = 'none';
    if (confirm) confirm.style.display = 'none';
    if (progress) progress.style.display = 'none';
    if (result) result.style.display = 'none';
  }

  if (mode === 'running') {
    if (title) title.textContent = '\u23f3 Update wordt uitgevoerd...';
    hideAll();
    if (progress) progress.style.display = 'block';
    if (btn) { btn.disabled = true; btn.style.display = 'none'; }
    if (laterBtn) laterBtn.style.display = 'none';
    if (backBtn) backBtn.style.display = 'none';
    if (reviewLaterBtn) reviewLaterBtn.style.display = 'none';
    if (reviewConfirmBtn) reviewConfirmBtn.style.display = 'none';
    if (abortBtn) abortBtn.style.display = 'inline-block';
    if (rollbackBtn) rollbackBtn.style.display = 'none';
    if (footer) footer.textContent = 'Het systeem wordt geüpdatet. Even geduld...';
  } else if (mode === 'done') {
    if (title) title.textContent = '\u2705 Update voltooid';
    hideAll();
    if (result) result.style.display = 'block';
    if (btn) { btn.disabled = true; btn.style.display = 'none'; }
    if (laterBtn) laterBtn.style.display = 'none';
    if (backBtn) backBtn.style.display = 'none';
    if (reviewLaterBtn) reviewLaterBtn.style.display = 'none';
    if (reviewConfirmBtn) reviewConfirmBtn.style.display = 'none';
    if (abortBtn) abortBtn.style.display = 'none';
    if (rollbackBtn) rollbackBtn.style.display = 'none';
  } else if (mode === 'restarting') {
    if (title) title.textContent = '\u23f3 Server wordt herstart...';
    hideAll();
    if (progress) progress.style.display = 'block';
    if (btn) { btn.disabled = true; btn.style.display = 'none'; }
    if (laterBtn) laterBtn.style.display = 'none';
    if (backBtn) backBtn.style.display = 'none';
    if (reviewLaterBtn) reviewLaterBtn.style.display = 'none';
    if (reviewConfirmBtn) reviewConfirmBtn.style.display = 'none';
    if (abortBtn) abortBtn.style.display = 'none';
    if (rollbackBtn) rollbackBtn.style.display = 'none';
    if (footer) footer.textContent = 'De server wordt herstart. Even geduld... de pagina wordt zo herladen.';
  } else if (mode === 'error') {
    if (title) title.textContent = '\u274c Update mislukt';
    hideAll();
    if (result) result.style.display = 'block';
    if (confirm) confirm.style.display = 'block';
    if (btn) { btn.disabled = false; btn.style.display = 'inline-block'; btn.textContent = '\u2b06 Opnieuw proberen'; }
    if (laterBtn) laterBtn.style.display = 'none';
    if (backBtn) backBtn.style.display = 'none';
    if (reviewLaterBtn) reviewLaterBtn.style.display = 'none';
    if (reviewConfirmBtn) reviewConfirmBtn.style.display = 'none';
    if (abortBtn) abortBtn.style.display = 'none';
    if (rollbackBtn) rollbackBtn.style.display = 'inline-block';
    if (footer) footer.style.display = 'block';
  } else if (mode === 'rolling') {
    if (title) title.textContent = '\u23f3 Rollback bezig...';
    hideAll();
    if (progress) { progress.style.display = 'block'; }
    if (result) result.style.display = 'none';
    if (btn) { btn.disabled = true; btn.style.display = 'none'; }
    if (laterBtn) laterBtn.style.display = 'none';
    if (backBtn) backBtn.style.display = 'none';
    if (reviewLaterBtn) reviewLaterBtn.style.display = 'none';
    if (reviewConfirmBtn) reviewConfirmBtn.style.display = 'none';
    if (abortBtn) abortBtn.style.display = 'none';
    if (rollbackBtn) { rollbackBtn.disabled = true; rollbackBtn.style.display = 'inline-block'; }
    if (footer) footer.textContent = 'Systeem wordt teruggedraaid naar de vorige staat... Even geduld.';
  } else if (mode === 'aborted') {
    if (title) title.textContent = '\u26a0 Update geannuleerd';
    hideAll();
    if (result) result.style.display = 'block';
    if (confirm) confirm.style.display = 'block';
    if (btn) { btn.disabled = false; btn.style.display = 'inline-block'; btn.textContent = '\u2b06 Opnieuw proberen'; }
    if (laterBtn) laterBtn.style.display = 'none';
    if (backBtn) backBtn.style.display = 'none';
    if (reviewLaterBtn) reviewLaterBtn.style.display = 'none';
    if (reviewConfirmBtn) reviewConfirmBtn.style.display = 'none';
    if (abortBtn) abortBtn.style.display = 'none';
    if (rollbackBtn) rollbackBtn.style.display = 'none';
    if (footer) footer.style.display = 'block';
  } else {
    // Default: show review step
    if (title) title.textContent = '\u2b06 Update Available';
    if (review) review.style.display = 'block';
    if (confirm) confirm.style.display = 'none';
    if (progress) progress.style.display = 'none';
    if (result) result.style.display = 'none';
    if (btn) { btn.disabled = false; btn.style.display = 'none'; }
    if (laterBtn) laterBtn.style.display = 'none';
    if (backBtn) backBtn.style.display = 'none';
    if (reviewLaterBtn) reviewLaterBtn.style.display = 'inline-block';
    if (reviewConfirmBtn) { reviewConfirmBtn.disabled = false; reviewConfirmBtn.style.display = 'inline-block'; }
    if (abortBtn) abortBtn.style.display = 'none';
    if (rollbackBtn) rollbackBtn.style.display = 'none';
    if (footer) footer.style.display = 'none';
  }
}

function confirmUpdate() {
  var review = document.getElementById('updateReview');
  var confirm = document.getElementById('updateConfirm');
  if (review) review.style.display = 'none';
  if (confirm) confirm.style.display = 'block';
}

function backToReview() {
  var review = document.getElementById('updateReview');
  var confirm = document.getElementById('updateConfirm');
  if (review) review.style.display = 'block';
  if (confirm) confirm.style.display = 'none';
}

function _renderSteps(container, results) {
  var html = '';
  (results || []).forEach(function(r) {
    var icon = r.status === 'ok' ? '\u2705' : r.status === 'error' ? '\u274c' : '\u23f3';
    var isErr = r.status === 'error';
    var outputColor = isErr ? 'var(--danger-text)' : 'var(--text-secondary)';
    var outputBg = isErr ? 'var(--danger-bg)' : 'transparent';
    html += '<div style="margin-bottom:0.5rem;">' + icon + ' <strong>' + esc(r.step) + '</strong>';
    if (r.output) {
      html += '<br><pre style="margin:0.25rem 0 0 0;padding:0.5rem;font-size:0.8rem;white-space:pre-wrap;word-break:break-all;background:' + outputBg + ';color:' + outputColor + ';border-radius:4px;border:1px solid ' + (isErr ? 'var(--danger-text)' : 'var(--border-color)') + ';">' + esc(r.output) + '</pre>';
    }
    html += '</div>';
  });
  container.innerHTML = html;
}

function _pollUpdate(taskId) {
  var C = window.CMS || {};
  var statusUrl = (C.updateStatusUrl || '/cms/admin/update-status/') + taskId;
  var result = document.getElementById('updateResult');
  var steps = document.getElementById('updateSteps');

  _updatePollTimer = setInterval(function() {
    if (_updateTaskId !== taskId) { _stopPoll(); return; }
    apiFetch(statusUrl, { method: 'GET', headers: { 'Accept': 'application/json' } })
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (_updateTaskId !== taskId) return;
        if (data.status === 'running' || data.status === 'starting') {
          _renderSteps(steps, data.results);
        } else if (data.status === 'restarting') {
          _renderSteps(steps, data.results);
          _updateUI('restarting');
          setTimeout(function() { location.reload(); }, 4000);
        } else if (data.status === 'done') {
          _stopPoll();
          _renderSteps(steps, data.results);
          if (data.success) {
            _updateUI('done');
            result.innerHTML = '<div style="margin-top:1rem;padding:0.75rem;background:var(--success-bg);border-radius:6px;color:var(--success-text);">\u2705 ' + esc(data.message) + '</div>';
            setTimeout(function() { location.reload(); }, 3000);
          } else {
            _showUpdateError(result, data);
          }
        } else if (data.status === 'error') {
          _stopPoll();
          _renderSteps(steps, data.results);
          _showUpdateError(result, data);
        } else if (data.status === 'aborting' || data.aborted) {
          _stopPoll();
          _updateUI('aborted');
          result.innerHTML = '<div style="padding:0.75rem;background:#fef3c7;border:1px solid #f59e0b;border-radius:6px;color:#92400e;">\u26a0\ufe0f Update geannuleerd.</div>';
        }
      })
      .catch(function(err) {
        // Connection errors during restart are expected — keep polling
        console.warn('Poll error (may be restarting):', err.message);
      });
  }, 2000);
}

function _showUpdateError(resultContainer, data) {
  _updateUI('error');
  var errors = [];
  var html = '';
  (data.results || []).forEach(function(r) {
    if (r.status === 'error') errors.push(r.step + ': ' + (r.output || '(geen foutmelding)'));
  });
  var errorCount = errors.length;
  html += '<div style="margin-top:1rem;padding:0.75rem;background:#fef2f2;border:2px solid #dc2626;border-radius:6px;color:#991b1b;">\u274c <strong>Update had errors' + (errorCount ? ' (' + errorCount + ' ' + (errorCount === 1 ? 'fout' : 'fouten') + ')' : '') + '.</strong></div>';
  if (errors.length) {
    html += '<div style="margin-top:0.5rem;">';
    errors.forEach(function(e) {
      html += '<pre style="margin:0.25rem 0;padding:0.5rem;font-size:0.8rem;white-space:pre-wrap;word-break:break-all;background:#fef2f2;border:1px solid #fca5a5;border-radius:4px;color:#991b1b;">\u274c ' + esc(e) + '</pre>';
    });
    html += '</div>';
  } else {
    html += '<div style="margin-top:0.5rem;padding:0.75rem;background:#fef2f2;border:1px solid #fca5a5;border-radius:4px;color:#991b1b;">\u26a0\ufe0f Geen gedetailleerde foutmelding beschikbaar.</div>';
  }
  resultContainer.innerHTML = html;
}

function _stopPoll() {
  if (_updatePollTimer) { clearInterval(_updatePollTimer); _updatePollTimer = null; }
  _updateTaskId = null;
  window.__updateTaskId = null;
}

function runUpdate() {
  var C = window.CMS || {};
  var result = document.getElementById('updateResult');
  var steps = document.getElementById('updateSteps');

  if (!result || !steps) return;
  _updateUI('running');
  steps.innerHTML = '<div style="color:#666;">Update wordt gestart...</div>';

  apiFetch(C.doUpdateUrl || '/cms/admin/do-update', { method: 'POST', headers: { 'Accept': 'application/json' } })
    .then(function(r) {
      if (!r.ok && !r.headers.get('content-type')?.includes('json')) {
        throw new Error('Server returned ' + r.status + ' — mogelijk sessie verlopen? Ververs de pagina en probeer opnieuw.');
      }
      return r.json();
    })
    .then(function(data) {
      if (!data.task_id) throw new Error('Geen task_id ontvangen');
      _updateTaskId = data.task_id;
      window.__updateTaskId = data.task_id;
      _pollUpdate(data.task_id);
    })
    .catch(function(err) {
      _updateUI('error');
      result.style.display = 'block';
      result.innerHTML = '<div style="padding:0.75rem;background:var(--danger-bg);border-radius:6px;color:var(--danger-text);">\u274c Error: ' + esc(err.message) + '</div>';
    });
}

function cancelUpdate() {
  var tid = _updateTaskId || window.__updateTaskId;
  if (!tid) return;
  var C = window.CMS || {};
  var abortUrl = (C.abortUpdateUrl || '/cms/admin/abort-update/') + tid;
  var steps = document.getElementById('updateSteps');
  if (steps) steps.innerHTML = '<div style="color:#92400e;">\u26a0\ufe0f Annuleren... bezig met stoppen van de update.</div>';
  apiFetch(abortUrl, { method: 'POST', headers: { 'Accept': 'application/json' } }).catch(function() {});
}

function rollbackUpdate() {
  var C = window.CMS || {};
  var result = document.getElementById('updateResult');
  var steps = document.getElementById('updateSteps');

  if (!result || !steps) return;
  _updateUI('rolling');
  steps.innerHTML = '<div style="color:#666;">Backup wordt teruggedraaid...</div>';

  apiFetch(C.rollbackUrl || '/cms/admin/rollback-update', { method: 'POST', headers: { 'Accept': 'application/json' } })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      var html = '';
      (data.results || []).forEach(function(r) {
        var icon = r.status === 'ok' ? '\u2705' : r.status === 'error' ? '\u274c' : '\u23f3';
        html += '<div style="margin-bottom:0.5rem;">' + icon + ' <strong>' + r.step + '</strong>';
        if (r.output) {
          html += '<br><pre style="margin:0.25rem 0 0 0;padding:0.5rem;font-size:0.8rem;white-space:pre-wrap;word-break:break-all;background:' + (r.status === 'error' ? '#fef2f2' : 'transparent') + ';border-radius:4px;">' + esc(r.output) + '</pre>';
        }
        html += '</div>';
      });
      if (data.success) {
        html += '<div style="margin-top:1rem;padding:0.75rem;background:var(--success-bg);border-radius:6px;color:var(--success-text);">\u2705 ' + data.message + '</div>';
        html += '<p style="margin-top:0.5rem;font-size:0.85rem;">\u23f3 Pagina wordt herladen...</p>';
        _updateUI('done');
        result.innerHTML = html;
        setTimeout(function() { location.reload(); }, 2500);
      } else {
        _updateUI('error');
        html += '<div style="margin-top:1rem;padding:0.75rem;background:#fef2f2;border:2px solid #dc2626;border-radius:6px;color:#991b1b;">\u274c Rollback mislukt. Neem contact op met de beheerder.</div>';
        result.innerHTML = html;
      }
    })
    .catch(function(err) {
      _updateUI('error');
      result.innerHTML = '<div style="padding:0.75rem;background:var(--danger-bg);border-radius:6px;color:var(--danger-text);">\u274c Rollback error: ' + esc(err.message) + '</div>';
    });
}

function checkForUpdates() {
  var statusEl = document.getElementById('updateCheckStatus');
  if (!statusEl) return;
  statusEl.textContent = '\u23f3 Checking...';
  fetch('/cms/api/check-update?force=1', { headers: { 'Accept': 'application/json' } })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.update_available) {
        statusEl.innerHTML = '\u2705 <strong>' + (data.version_update ? 'Update ' + data.current_version + ' \u2192 ' + data.latest_version : 'New commits') + '</strong> \u2014 <a href="#" onclick="showUpdateModal();return false;" style="color:var(--link-color);">' + (data.version_update ? 'Click to update' : 'Click to update') + '</a>';
        var banner = document.getElementById('updateBanner');
        var text = document.getElementById('updateBannerText');
        if (banner && text) {
          text.textContent = data.version_update ? 'Update ' + data.current_version + ' \u2192 ' + data.latest_version + ' available.' : 'New commits available.';
          banner.style.display = 'block';
        }
        if (data.changelog) {
          var clEl = document.getElementById('updateChangelog');
          if (clEl) {
            var lines = data.changelog.split('\n').filter(Boolean);
            var clHtml = lines.map(function(l) {
              if (l.startsWith('### ')) return '<b>' + esc(l.slice(4)) + '</b>';
              if (l.startsWith('- ')) return '\u2022 ' + esc(l.slice(2));
              if (l.startsWith('## ')) return '<b style="font-size:1.1rem;">' + esc(l.replace(/[\[\]]/g,'')) + '</b>';
              return esc(l);
            }).join('<br>');
            clEl.innerHTML = '<strong>\U0001f4cb What\'s new:</strong><div style="margin-top:0.5rem;">' + clHtml + '</div>';
            clEl.style.display = 'block';
          }
        }
        document.getElementById('updCurrent').textContent = data.current_version;
        document.getElementById('updLatest').textContent = data.version_update ? data.latest_version : data.current_version + ' (+commits)';
        document.getElementById('updateReview').style.display = 'block';
      } else if (data.check_enabled === false) {
        statusEl.innerHTML = '\u26a0\ufe0f ' + (data.message || 'Update checking not configured. Set update_check_repo in settings.');
      } else {
        statusEl.textContent = '\u2705 Up to date \u2014 ' + data.current_version;
      }
    })
    .catch(function(err) {
      statusEl.innerHTML = '\u274c Error: ' + (err.message || 'check failed');
    });
}
