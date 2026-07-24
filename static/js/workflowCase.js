/* ── Workflow Case JS — data-click handlers ──────────────── */

/* Postcode check via kadaster-lookup */
window.wfPostcodeCheck = function(btn) {
  var entry = btn.closest('.address-entry');
  if (!entry) return;
  var zipcode = entry.querySelector('.addr-zipcode').value.trim();
  var number = entry.querySelector('.addr-number').value.trim();
  if (!zipcode || !number) {
    alert('Enter zipcode and house number first');
    return;
  }
  btn.disabled = true;
  btn.textContent = '\u23f3';
  apiFetch('/cms/api/kadaster-lookup', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ zipcode: zipcode, number: number })
  })
  .then(function(r) { return r.json(); })
  .then(function(data) {
    if (data.found && data.bag_data) {
      var b = data.bag_data;
      if (b.street) entry.querySelector('.addr-street').value = b.street;
      if (b.town) entry.querySelector('.addr-town').value = b.town;
      if (b.number) entry.querySelector('.addr-number').value = b.number;
      btn.textContent = '\u2705';
    } else {
      btn.textContent = '\u274c';
    }
    setTimeout(function() { btn.textContent = '\ud83d\udd0d'; btn.disabled = false; }, 2000);
  })
  .catch(function() {
    btn.textContent = '\u274c';
    setTimeout(function() { btn.textContent = '\ud83d\udd0d'; btn.disabled = false; }, 2000);
  });
};

/* Add address row */
window.wfAddAddress = function(btn) {
  var container = btn.previousElementSibling;
  if (!container || !container.classList.contains('addresses-container')) return;
  var first = container.querySelector('.address-entry');
  if (!first) return;
  var rowIdx = container.querySelectorAll('.address-entry').length;
  var clone = first.cloneNode(true);
  clone.querySelectorAll('input').forEach(function(el) {
    if (el.type !== 'checkbox') el.value = '';
    else el.checked = false;
  });
  var lookupBtn = clone.querySelector('[data-click="wfPostcodeCheck"]');
  if (lookupBtn) lookupBtn.setAttribute('data-row', rowIdx);
  container.appendChild(clone);
};

/* Add contact row */
window.wfAddContact = function(btn) {
  var container = btn.previousElementSibling;
  if (!container || !container.classList.contains('contacts-container')) return;
  var first = container.querySelector('.contact-entry');
  if (!first) return;
  var clone = first.cloneNode(true);
  clone.querySelectorAll('input, select').forEach(function(el) {
    if (el.type !== 'checkbox') el.value = '';
    else el.checked = false;
  });
  container.appendChild(clone);
};

/* Resolve prefix: data-sid > data-newidx > data-idx */
function _wfResolvePrefix(btn) {
  var sid = btn.dataset.sid;
  var newidx = btn.dataset.newidx;
  var idx = btn.dataset.idx;
  if (sid) return 'subj_' + sid + '_';
  if (newidx !== undefined) return 'subj_new_' + newidx + '_';
  if (idx !== undefined) return 'subject_' + idx + '_';
  return 'subject_0_';
}
function _wfResolveContainer(btn, cls) {
  var parent = btn.closest('.sf-company') || btn.closest('.sf-vehicle') || btn.closest('.sf-vessel');
  if (!parent) return null;
  return parent.querySelector('.' + cls);
}

/* KVK company lookup */
window.wfKvkLookup = function(btn) {
  var queryInput = btn.parentElement.querySelector('[data-kvk-query]');
  if (!queryInput) return;
  var q = queryInput.value.trim();
  if (!q) return;
  var statusEl = _wfResolveContainer(btn, 'kvk-lookup-status');
  var prefix = _wfResolvePrefix(btn);
  btn.disabled = true;
  btn.textContent = '\u23f3 Searching...';
  if (statusEl) statusEl.textContent = 'Searching KVK...';
  apiFetch('/cms/api/kvk-lookup', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query: q })
  })
  .then(function(r) { return r.json(); })
  .then(function(data) {
    btn.disabled = false;
    btn.textContent = '\ud83d\udd0d KVK Search';
    if (data.error) {
      if (statusEl) statusEl.textContent = data.error;
      return;
    }
    if (data.result) {
      var r = data.result;
      function set(k, v) { var el = document.querySelector('[name="' + prefix + k + '"]'); if (el) el.value = v || ''; }
      set('name', r.naam || r.handelsnaam || '');
      set('registration_number', r.kvk_nummer || '');
      set('legal_form', r.rechtsvorm || '');
      set('street', r.straat || '');
      set('house_number', r.huisnummer || '');
      set('city', r.plaats || '');
      set('postal_code', r.postcode || '');
      if (statusEl) statusEl.textContent = '\u2705 Found: ' + (r.handelsnaam || r.naam || q);
    }
  })
  .catch(function() {
    btn.disabled = false;
    btn.textContent = '\ud83d\udd0d KVK Search';
    if (statusEl) statusEl.textContent = 'Error during lookup';
  });
};

/* RDW vehicle lookup */
window.wfRdwLookup = function(btn) {
  var kentekenInput = btn.parentElement.querySelector('[data-rdw-kenteken]');
  if (!kentekenInput) return;
  var kenteken = kentekenInput.value.trim().replace(/[^a-zA-Z0-9]/g, '');
  if (!kenteken) return;
  var statusEl = _wfResolveContainer(btn, 'rdw-status');
  var prefix = _wfResolvePrefix(btn);
  btn.disabled = true;
  btn.textContent = '\u23f3 Fetching...';
  if (statusEl) statusEl.textContent = 'Fetching RDW data...';
  apiFetch('/check-rdw-vehicle', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kenteken: kenteken })
  })
  .then(function(r) { return r.json(); })
  .then(function(data) {
    btn.disabled = false;
    btn.textContent = '\ud83d\udce1 Fetch RDW';
    if (data.error) {
      if (statusEl) statusEl.textContent = data.error;
      return;
    }
    if (data.found && data.vehicle) {
      var v = data.vehicle;
      function set(k, val) { var el = document.querySelector('[name="' + prefix + k + '"]'); if (el) el.value = val || ''; }
      set('name', v.merk || '');
      set('identification', v.kenteken || kenteken);
      set('vin', v.chassisnummer || '');
      set('brand', v.merk || '');
      set('handelsbenaming', v.handelsbenaming || '');
      set('vehicle_type', v.inrichting || '');
      set('eerste_kleur', v.eerste_kleur || '');
      set('voertuigsoort', v.voertuigsoort || '');
      set('aantal_deuren', v.aantal_deuren || '');
      set('aantal_zitplaatsen', v.aantal_zitplaatsen || '');
      set('vervaldatum_apk', v.vervaldatum_apk || '');
      set('wam_verzekerd', v.wam_verzekerd || '');
      if (statusEl) statusEl.textContent = '\u2705 Found: ' + (v.handelsbenaming || v.merk || kenteken);
    }
  })
  .catch(function() {
    btn.disabled = false;
    btn.textContent = '\ud83d\udce1 Fetch RDW';
    if (statusEl) statusEl.textContent = 'Error during lookup';
  });
};

/* Vessel lookup */
window.wfVesselLookup = function(btn) {
  var wrap = btn.parentElement;
  var imo = (wrap.querySelector('[data-vessel-imo]') || {}).value || '';
  var mmsi = (wrap.querySelector('[data-vessel-mmsi]') || {}).value || '';
  var eni = (wrap.querySelector('[data-vessel-eni]') || {}).value || '';
  imo = imo.trim(); mmsi = mmsi.trim(); eni = eni.trim();
  if (!imo && !mmsi && !eni) return;
  var statusEl = _wfResolveContainer(btn, 'vessel-lookup-status');
  var prefix = _wfResolvePrefix(btn);
  btn.disabled = true;
  btn.textContent = '\u23f3 Searching...';
  if (statusEl) statusEl.textContent = 'Searching vessel data...';
  var payload = {};
  if (imo) payload.imo = imo;
  if (mmsi) payload.mmsi = mmsi;
  if (eni) payload.eni = eni;
  apiFetch('/cms/api/vessel-lookup', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  })
  .then(function(r) { return r.json(); })
  .then(function(data) {
    btn.disabled = false;
    btn.textContent = '\ud83d\udd0d Search Ship';
    if (data.error) {
      if (statusEl) statusEl.textContent = data.error;
      return;
    }
    if (data.found && data.vessel) {
      var v = data.vessel;
      function set(k, val) { var el = document.querySelector('[name="' + prefix + k + '"]'); if (el) el.value = val || ''; }
      set('name', v.name || v.SHIPNAME || '');
      set('imo_number', v.imo || v.IMO || imo);
      set('mmsi', v.mmsi || v.MMSI || mmsi);
      set('eni_number', v.eni || v.ENI || eni);
      set('vessel_nationality', v.flag || v.FLAGSTATE || '');
      if (statusEl) statusEl.textContent = '\u2705 Found: ' + (v.name || v.SHIPNAME || 'vessel');
    }
  })
  .catch(function() {
    btn.disabled = false;
    btn.textContent = '\ud83d\udd0d Search Ship';
    if (statusEl) statusEl.textContent = 'Error during lookup';
  });
};
