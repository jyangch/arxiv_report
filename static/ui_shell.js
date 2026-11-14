/* Shared UI shell helpers: manual/system theme sync, keyboard shortcuts, and calendar popover. */
(function() {
  var THEME_KEY = 'arxiv-report:theme';
  var LANG_KEY = 'arxiv-report:lang';

  function currentLang() {
    try {
      var saved = localStorage.getItem(LANG_KEY);
      if (saved === 'zh' || saved === 'en') return saved;
    } catch (_e) {}
    if (document.documentElement && document.documentElement.dataset && document.documentElement.dataset.lang) {
      return document.documentElement.dataset.lang === 'en' ? 'en' : 'zh';
    }
    return 'zh';
  }

  function broadcastLang(lang) {
    document.querySelectorAll('iframe').forEach(function(frame) {
      try {
        if (frame.contentWindow) {
          frame.contentWindow.postMessage({type: 'arxiv-lang-change', lang: lang}, '*');
        }
      } catch (_e) {}
    });
  }

  function applyLang(lang, persist) {
    var norm = lang === 'en' ? 'en' : 'zh';
    if (document.documentElement && document.documentElement.dataset) {
      document.documentElement.dataset.lang = norm;
    }
    if (persist) {
      try { localStorage.setItem(LANG_KEY, norm); } catch (_e) {}
    }
    var btn = document.getElementById('lang-toggle');
    if (btn) {
      btn.classList.toggle('is-en', norm === 'en');
      btn.setAttribute('aria-pressed', norm === 'en' ? 'true' : 'false');
      btn.title = norm === 'en' ? 'Switch content language to Chinese (中文)' : 'Switch content language to English (EN)';
    }
    broadcastLang(norm);
  }

  function initLangToggle() {
    applyLang(currentLang(), false);
    var btn = document.getElementById('lang-toggle');
    if (btn) {
      btn.addEventListener('click', function() {
        var next = currentLang() === 'en' ? 'zh' : 'en';
        applyLang(next, true);
      });
    }
    window.addEventListener('storage', function(e) {
      if (e.key === LANG_KEY && (e.newValue === 'zh' || e.newValue === 'en')) {
        applyLang(e.newValue, false);
      }
    });
  }

  function systemTheme() {
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches
      ? 'dark'
      : 'light';
  }

  function currentTheme() {
    try {
      var saved = localStorage.getItem(THEME_KEY);
      if (saved === 'light' || saved === 'dark') return saved;
    } catch (_e) {}
    if (document.documentElement && document.documentElement.dataset && document.documentElement.dataset.theme) {
      return document.documentElement.dataset.theme;
    }
    return systemTheme();
  }

  function broadcastTheme(theme) {
    document.querySelectorAll('iframe').forEach(function(frame) {
      try {
        if (frame.contentWindow) {
          frame.contentWindow.postMessage({type: 'arxiv-theme-change', theme: theme}, '*');
        }
      } catch (_e) {}
    });
  }

  function syncReportIframeHash() {
    var loc = (typeof window !== 'undefined' && window.location) ? window.location : null;
    var raw = String((loc && loc.hash) || '').replace(/^#/, '');
    if (!/^p\d+$/.test(raw)) return;
    document.querySelectorAll('iframe').forEach(function(frame) {
      try {
        if (frame.contentWindow) {
          frame.contentWindow.postMessage({type: 'arxiv-scroll-target', targetId: raw}, '*');
        }
      } catch (_e) {}
    });
  }

  function bindIframeLoadSync() {
    document.querySelectorAll('iframe').forEach(function(frame) {
      if (frame.dataset && frame.dataset.hashSyncBound === '1') return;
      if (frame.dataset) frame.dataset.hashSyncBound = '1';
      frame.addEventListener('load', function() {
        broadcastTheme(currentTheme());
        broadcastLang(currentLang());
        syncReportIframeHash();
      });
    });
  }

  function applyTheme(theme, persist) {
    if (document.documentElement) {
      document.documentElement.dataset.theme = theme;
    }
    if (persist) {
      try { localStorage.setItem(THEME_KEY, theme); } catch (_e) {}
    }
    var btn = document.getElementById('theme-toggle');
    if (btn) {
      btn.classList.toggle('is-dark', theme === 'dark');
      btn.setAttribute('aria-pressed', theme === 'dark' ? 'true' : 'false');
      btn.title = theme === 'dark' ? 'Switch to Light Paper Theme' : 'Switch to Deep Space Dark Theme';
    }
    broadcastTheme(theme);
  }

  function initThemeToggle() {
    applyTheme(currentTheme(), false);
    var btn = document.getElementById('theme-toggle');
    if (btn) {
      btn.addEventListener('click', function() {
        var next = currentTheme() === 'dark' ? 'light' : 'dark';
        applyTheme(next, true);
      });
    }
    bindIframeLoadSync();
    window.addEventListener('storage', function(e) {
      if (e.key === THEME_KEY && (e.newValue === 'light' || e.newValue === 'dark')) {
        applyTheme(e.newValue, false);
      }
    });
  }

  function initKeyboardShortcuts() {
    document.addEventListener('keydown', function(e) {
      if (e.key === 'Escape') {
        var pop = document.getElementById('cal-popover');
        var trig = document.getElementById('cal-trigger-btn');
        if (pop && !pop.hidden) {
          pop.hidden = true;
          if (trig) trig.setAttribute('aria-expanded', 'false');
        }
        return;
      }
      if (e.key === '/' && !e.metaKey && !e.ctrlKey && !e.altKey) {
        var active = document.activeElement;
        var tag = active ? active.tagName : '';
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (active && active.isContentEditable)) {
          return;
        }
        var searchEl = document.getElementById('global-search-input');
        if (searchEl) {
          e.preventDefault();
          searchEl.focus();
          searchEl.select();
        }
      }
    });
  }

  function pad2(n) {
    return n < 10 ? '0' + n : String(n);
  }

  function isoDateUTC(d) {
    return d.getUTCFullYear() + '-' + pad2(d.getUTCMonth() + 1) + '-' + pad2(d.getUTCDate());
  }

  function isoWeekUTC(d) {
    var tmp = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
    var dayNum = tmp.getUTCDay() || 7;
    tmp.setUTCDate(tmp.getUTCDate() + 4 - dayNum);
    var yearStart = new Date(Date.UTC(tmp.getUTCFullYear(), 0, 1));
    var weekNo = Math.ceil(((tmp - yearStart) / 86400000 + 1) / 7);
    return tmp.getUTCFullYear() + '-W' + pad2(weekNo);
  }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function initNavCalendar() {
    var data = window.__ARXIV_NAV__;
    var trig = document.getElementById('cal-trigger-btn');
    var pop = document.getElementById('cal-popover');
    var wrap = document.getElementById('cal-wrap');
    if (!data || !trig || !pop || !wrap) return;

    var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    var WDAYS = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'];
    var dayOrder = data.dayOrder || [];
    var weekOrder = data.weekOrder || [];
    var monthOrder = data.monthOrder || [];
    var yearOrder = data.yearOrder || [];
    var daysMap = data.days || {};
    var weeksMap = data.weeks || {};
    var monthsMap = data.months || {};
    var yearsMap = data.years || {};

    var latestDay = data.latestDay || (dayOrder[0] || '');
    var minDay = dayOrder.length ? dayOrder[dayOrder.length - 1] : latestDay;
    var maxDay = latestDay;

    var calTab = (data.mode === 'week' || data.mode === 'month' || data.mode === 'year') ? data.mode : 'day';
    var viewYear = 2026;
    var viewMonth = 0;

    function syncInitialYM() {
      var anchor = '';
      if (data.mode === 'day' && /^\d{4}-\d{2}-\d{2}$/.test(data.currentKey || '')) {
        anchor = data.currentKey;
      } else if (data.mode === 'week' && data.currentKey) {
        for (var i = 0; i < dayOrder.length; i += 1) {
          var d = dayOrder[i];
          if (daysMap[d] && daysMap[d].week === data.currentKey) {
            anchor = d;
            break;
          }
        }
      } else if (data.mode === 'month' && /^\d{4}-\d{2}$/.test(data.currentKey || '')) {
        anchor = data.currentKey + '-01';
      } else if (data.mode === 'year' && /^\d{4}$/.test(data.currentKey || '')) {
        anchor = data.currentKey + '-01-01';
      }
      if (!anchor) anchor = latestDay || new Date().toISOString().slice(0, 10);
      viewYear = parseInt(anchor.slice(0, 4), 10) || 2026;
      viewMonth = (parseInt(anchor.slice(5, 7), 10) || 1) - 1;
    }
    syncInitialYM();

    function minYMIndex() {
      if (!minDay) return 0;
      return parseInt(minDay.slice(0, 4), 10) * 12 + parseInt(minDay.slice(5, 7), 10) - 1;
    }

    function maxYMIndex() {
      if (!maxDay) return 999999;
      return parseInt(maxDay.slice(0, 4), 10) * 12 + parseInt(maxDay.slice(5, 7), 10) - 1;
    }

    function renderPopover() {
      pop.innerHTML = '';

      function makeStepBtn(dir, label) {
        var b = el('button', 'cal-step');
        b.type = 'button';
        b.setAttribute('aria-label', label);
        b.innerHTML =
          '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="' +
          (dir < 0 ? '15 18 9 12 15 6' : '9 6 15 12 9 18') +
          '"/></svg>';
        return b;
      }

      var tabRow = el('div', 'cal-tabs');
      [
        ['day', 'Day'],
        ['week', 'Week'],
        ['month', 'Month'],
        ['year', 'Year']
      ].forEach(function(pair) {
        var b = el('button', 'cal-tab' + (calTab === pair[0] ? ' is-active' : ''), pair[1]);
        b.type = 'button';
        b.addEventListener('click', function(e) {
          e.stopPropagation();
          calTab = pair[0];
          renderPopover();
        });
        tabRow.appendChild(b);
      });
      pop.appendChild(tabRow);

      if (calTab === 'year') {
        var yGrid = el('div', 'cal-years-grid');
        if (!yearOrder.length) {
          yGrid.appendChild(el('p', 'cal-empty', 'No archived years yet.'));
        } else {
          yearOrder.forEach(function(yk) {
            var info = yearsMap[yk] || {};
            var btn = el('button', 'cal-period-cell' + (data.mode === 'year' && data.currentKey === yk ? ' is-sel' : ''));
            btn.type = 'button';
            btn.appendChild(el('span', 'cal-p-lbl', yk));
            btn.appendChild(el('span', 'cal-p-cnt', (info.count || 0) + ' papers'));
            btn.addEventListener('click', function() {
              window.location.href = '/summary/year/' + encodeURIComponent(yk);
            });
            yGrid.appendChild(btn);
          });
        }
        pop.appendChild(yGrid);
        return;
      }

      if (calTab === 'month') {
        var mHead = el('div', 'cal-head');
        var prevY = makeStepBtn(-1, 'Previous year');
        var nextY = makeStepBtn(1, 'Next year');
        var minY = minDay ? parseInt(minDay.slice(0, 4), 10) : viewYear;
        var maxY = maxDay ? parseInt(maxDay.slice(0, 4), 10) : viewYear;
        prevY.disabled = viewYear <= minY;
        nextY.disabled = viewYear >= maxY;
        prevY.addEventListener('click', function(e) {
          e.stopPropagation();
          viewYear -= 1;
          renderPopover();
        });
        nextY.addEventListener('click', function(e) {
          e.stopPropagation();
          viewYear += 1;
          renderPopover();
        });
        mHead.appendChild(prevY);
        mHead.appendChild(el('span', 'cal-head-title', String(viewYear)));
        mHead.appendChild(nextY);
        pop.appendChild(mHead);

        var mGrid = el('div', 'cal-months-grid');
        for (var mIdx = 0; mIdx < 12; mIdx += 1) {
          var mk = viewYear + '-' + pad2(mIdx + 1);
          var minfo = monthsMap[mk];
          if (minfo) {
            var mb = el('button', 'cal-period-cell' + (data.mode === 'month' && data.currentKey === mk ? ' is-sel' : ''));
            mb.type = 'button';
            mb.appendChild(el('span', 'cal-p-lbl', MONTHS[mIdx]));
            mb.appendChild(el('span', 'cal-p-cnt', String(minfo.count || 0)));
            (function(targetKey) {
              mb.addEventListener('click', function() {
                window.location.href = '/summary/month/' + encodeURIComponent(targetKey);
              });
            })(mk);
            mGrid.appendChild(mb);
          } else {
            var ms = el('span', 'cal-period-cell is-disabled');
            ms.appendChild(el('span', 'cal-p-lbl', MONTHS[mIdx]));
            ms.appendChild(el('span', 'cal-p-cnt', '·'));
            mGrid.appendChild(ms);
          }
        }
        pop.appendChild(mGrid);
        return;
      }

      // Day or Week 8-column calendar matrix
      if (calTab === 'day') {
        var jumpForm = el('form', 'cal-jump-row');
        var jumpInput = el('input', 'cal-jump-input');
        jumpInput.type = 'date';
        jumpInput.value = (data.mode === 'day' && data.currentKey) ? data.currentKey : (maxDay || '');
        if (minDay) jumpInput.min = minDay;
        if (maxDay) jumpInput.max = maxDay;
        jumpInput.setAttribute('aria-label', 'Jump to archived digest date');
        var jumpBtn = el('button', 'cal-jump-btn', 'Go');
        jumpBtn.type = 'submit';
        var jumpMsg = el('span', 'cal-jump-msg');
        jumpForm.appendChild(jumpInput);
        jumpForm.appendChild(jumpBtn);
        jumpForm.addEventListener('submit', function(e) {
          e.preventDefault();
          var targetDay = String(jumpInput.value || '').trim();
          if (!targetDay) return;
          if (!daysMap[targetDay]) {
            jumpMsg.textContent = 'No archived digest on ' + targetDay;
            jumpMsg.hidden = false;
            return;
          }
          window.location.href = '/r/' + encodeURIComponent(targetDay);
        });
        pop.appendChild(jumpForm);
        jumpMsg.hidden = true;
        pop.appendChild(jumpMsg);
      }
      var head = el('div', 'cal-head');
      var prevM = makeStepBtn(-1, 'Previous month');
      var nextM = makeStepBtn(1, 'Next month');
      var curYM = viewYear * 12 + viewMonth;
      prevM.disabled = minDay && curYM <= minYMIndex();
      nextM.disabled = maxDay && curYM >= maxYMIndex();
      prevM.addEventListener('click', function(e) {
        e.stopPropagation();
        if (viewMonth === 0) {
          viewMonth = 11;
          viewYear -= 1;
        } else {
          viewMonth -= 1;
        }
        renderPopover();
      });
      nextM.addEventListener('click', function(e) {
        e.stopPropagation();
        if (viewMonth === 11) {
          viewMonth = 0;
          viewYear += 1;
        } else {
          viewMonth += 1;
        }
        renderPopover();
      });
      head.appendChild(prevM);
      head.appendChild(el('span', 'cal-head-title', MONTHS[viewMonth] + ' ' + viewYear));
      head.appendChild(nextM);
      pop.appendChild(head);

      var grid = el('div', 'cal-grid');
      grid.appendChild(el('span', 'cal-wd', 'Wk'));
      WDAYS.forEach(function(w) {
        grid.appendChild(el('span', 'cal-wd', w));
      });

      var first = new Date(Date.UTC(viewYear, viewMonth, 1));
      var last = new Date(Date.UTC(viewYear, viewMonth + 1, 0));
      var offset = (first.getUTCDay() + 6) % 7;
      var cursor = new Date(first);
      cursor.setUTCDate(first.getUTCDate() - offset);

      for (var row = 0; row < 6; row += 1) {
        var monday = new Date(cursor);
        monday.setUTCDate(cursor.getUTCDate() + row * 7);
        if (monday > last) break;

        var wkKey = isoWeekUTC(monday);
        var wkInfo = weeksMap[wkKey];
        var wkSel = data.mode === 'week' && data.currentKey === wkKey;
        if (wkInfo) {
          var wkBtn = el('button', 'cal-wk has-data' + (wkSel ? ' is-sel' : ''), wkKey.split('-')[1]);
          wkBtn.type = 'button';
          wkBtn.title = wkKey + ' · ' + (wkInfo.count || 0) + ' papers';
          (function(targetWeek) {
            wkBtn.addEventListener('click', function() {
              window.location.href = '/summary/week/' + encodeURIComponent(targetWeek);
            });
          })(wkKey);
          grid.appendChild(wkBtn);
        } else {
          grid.appendChild(el('span', 'cal-wk is-empty', wkKey.split('-')[1]));
        }

        for (var col = 0; col < 7; col += 1) {
          var cellDate = new Date(monday);
          cellDate.setUTCDate(monday.getUTCDate() + col);
          var dStr = isoDateUTC(cellDate);
          var dInfo = daysMap[dStr];
          var isOut = cellDate.getUTCMonth() !== viewMonth;
          var inSelWeek = data.mode === 'week' && data.currentKey === wkKey;
          var isSelDay = data.mode === 'day' && data.currentKey === dStr;

          if (dInfo) {
            var dBtn = el(
              'button',
              'cal-day has-report' + (isOut ? ' is-out' : '') + (isSelDay ? ' is-sel' : '') + (inSelWeek ? ' is-week-hi' : ''),
              String(cellDate.getUTCDate())
            );
            dBtn.type = 'button';
            dBtn.title = dStr + ' · ' + (dInfo.count || 0) + ' papers';
            (function(targetDay, targetWk) {
              dBtn.addEventListener('click', function() {
                if (calTab === 'week' && weeksMap[targetWk]) {
                  window.location.href = '/summary/week/' + encodeURIComponent(targetWk);
                } else {
                  window.location.href = '/r/' + encodeURIComponent(targetDay);
                }
              });
            })(dStr, wkKey);
            grid.appendChild(dBtn);
          } else {
            grid.appendChild(
              el(
                'span',
                'cal-day is-empty' + (isOut ? ' is-out' : '') + (inSelWeek ? ' is-week-hi' : ''),
                String(cellDate.getUTCDate())
              )
            );
          }
        }
      }
      pop.appendChild(grid);
    }

    trig.addEventListener('click', function(e) {
      e.stopPropagation();
      var willOpen = pop.hidden;
      if (willOpen) {
        renderPopover();
        pop.hidden = false;
        trig.setAttribute('aria-expanded', 'true');
      } else {
        pop.hidden = true;
        trig.setAttribute('aria-expanded', 'false');
      }
    });

    pop.addEventListener('click', function(e) {
      e.stopPropagation();
    });

    document.addEventListener('click', function(e) {
      if (!pop.hidden && !wrap.contains(e.target)) {
        pop.hidden = true;
        trig.setAttribute('aria-expanded', 'false');
      }
    });
  }

  function initGenerateGuard() {
    var details = document.getElementById('gen-details');
    var form = document.getElementById('generate-form');
    var dateInput = document.getElementById('date-input');
    var forceInput = document.getElementById('generate-force-input');
    var hint = document.getElementById('gen-date-hint');
    if (!form || !dateInput || form.dataset.guardBound === '1') return;
    form.dataset.guardBound = '1';

    var nav = window.__ARXIV_NAV__ || {};
    var daysMap = nav.days || {};

    function updateHint() {
      if (forceInput) forceInput.value = '';
      if (!hint) return;
      var val = String(dateInput.value || '').trim();
      var info = daysMap[val];
      if (!val || !info) {
        hint.hidden = true;
        hint.innerHTML = '';
        return;
      }
      hint.innerHTML = '';
      var msg = el('span', '', 'Digest already exists (' + (info.count || 0) + ' papers) · ');
      var link = el('a', '', 'Open existing \u2192');
      link.href = '/r/' + encodeURIComponent(val);
      hint.appendChild(msg);
      hint.appendChild(link);
      hint.hidden = false;
    }

    dateInput.addEventListener('input', updateHint);
    dateInput.addEventListener('change', updateHint);
    updateHint();

    form.addEventListener('submit', function(e) {
      var val = String(dateInput.value || '').trim();
      if (val && daysMap[val] && forceInput && forceInput.value !== '1') {
        var ok = window.confirm(
          'A daily digest for ' + val + ' already exists. Regenerate and overwrite it?'
        );
        if (!ok) {
          e.preventDefault();
          e.stopImmediatePropagation();
          return;
        }
        forceInput.value = '1';
      }
      if (details) details.open = false;
    }, true);
  }

  function boot() {
    initLangToggle();
    initThemeToggle();
    syncReportIframeHash();
    initKeyboardShortcuts();
    initNavCalendar();
    initGenerateGuard();
    window.addEventListener('hashchange', syncReportIframeHash);
    document.addEventListener('click', function(e) {
      var details = document.getElementById('gen-details');
      if (details && details.open && !details.contains(e.target)) {
        details.open = false;
      }
    });
    document.addEventListener('keydown', function(e) {
      if (e.key === 'Escape') {
        var details = document.getElementById('gen-details');
        if (details && details.open) details.open = false;
      }
    });
    if (document.body) {
      document.body.addEventListener('htmx:afterSwap', function() {
        initGenerateGuard();
        bindIframeLoadSync();
        syncReportIframeHash();
      });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
