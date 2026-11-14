// Mirror starred / Obsidian paper state between localStorage and the server,
// and upgrade legacy Craft buttons on historical report pages to Obsidian.
(function() {
    var PREFIX = 'arxiv-report:';
    var MIGRATED_KEY = 'arxiv-report-sync:migrated';
    var UNMARKED_PREFIX = 'arxiv-report-obsidian-unmarked:';
    var CAN_WRITE = window.__ARXIV_CAN_WRITE__ !== false;
    var ENDPOINT = '/api/paper-state';
    var OBSIDIAN_SAVE_ENDPOINT = '/api/obsidian/save';
    var OBSIDIAN_SAVED_ENDPOINT = '/api/obsidian/saved';

    var storage;
    try {
        storage = window.localStorage;
    } catch (_err) {
        return;
    }
    var proto = Object.getPrototypeOf(storage);
    var rawSetItem = proto.setItem;
    var rawRemoveItem = proto.removeItem;

    function kindOf(key) {
        return key.slice(key.lastIndexOf(':') + 1);
    }

    function baseOf(key) {
        return key.slice(0, key.lastIndexOf(':'));
    }

    function isFlagKey(key) {
        var kind = kindOf(key);
        return key.indexOf(PREFIX) === 0
            && (kind === 'starred' || kind === 'obsidian' || kind === 'crafted');
    }

    function isFlagged(base) {
        return storage.getItem(base + ':starred') === '1'
            || storage.getItem(base + ':obsidian') === '1'
            || storage.getItem(base + ':crafted') === '1';
    }

    function post(setItems, removeKeys) {
        fetch(ENDPOINT, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({set: setItems, remove: removeKeys}),
            keepalive: true
        }).catch(function(err) {
            console.warn('paper state sync failed', err);
        });
    }

    function pullJsonSync(url) {
        var xhr = new XMLHttpRequest();
        xhr.open('GET', url, false);
        xhr.send();
        if (xhr.status !== 200) throw new Error('HTTP ' + xhr.status);
        return JSON.parse(xhr.responseText);
    }

    function localKeys() {
        var keys = [];
        for (var i = 0; i < storage.length; i += 1) {
            var key = storage.key(i);
            if (key && key.indexOf(PREFIX) === 0) keys.push(key);
        }
        return keys;
    }

    var server = null;
    if (CAN_WRITE) {
        try {
            server = pullJsonSync(ENDPOINT);
        } catch (err) {
            console.warn('paper state pull failed; using browser state only', err);
        }
    }

    var savedObsidianIds = [];
    if (CAN_WRITE) {
        try {
            var savedPayload = pullJsonSync(OBSIDIAN_SAVED_ENDPOINT);
            if (savedPayload && Array.isArray(savedPayload.arxiv_ids)) {
                savedObsidianIds = savedPayload.arxiv_ids;
            }
        } catch (err) {
            console.warn('obsidian saved-id pull failed', err);
        }
    }
    window.__OBSIDIAN_SAVED_IDS__ = savedObsidianIds;

    if (server) {
        var migrated = storage.getItem(MIGRATED_KEY) === '1';
        var upload = {};
        localKeys().forEach(function(key) {
            if (!isFlagKey(key) || Object.prototype.hasOwnProperty.call(server, key)) return;
            if (!migrated && storage.getItem(key) === '1') {
                upload[key] = '1';
                var paper = storage.getItem(baseOf(key) + ':paper');
                if (paper) upload[baseOf(key) + ':paper'] = paper;
            } else {
                rawRemoveItem.call(storage, key);
            }
        });
        Object.keys(server).forEach(function(key) {
            rawSetItem.call(storage, key, server[key]);
        });
        if (Object.keys(upload).length) post(upload, []);
        rawSetItem.call(storage, MIGRATED_KEY, '1');
    }

    proto.setItem = function(key, value) {
        rawSetItem.call(this, key, value);
        if (!CAN_WRITE || this !== storage || typeof key !== 'string' || key.indexOf(PREFIX) !== 0) return;
        var items = {};
        if (isFlagKey(key)) {
            if (String(value) !== '1') {
                post({}, [key]);
                return;
            }
            items[key] = '1';
            var paper = storage.getItem(baseOf(key) + ':paper');
            if (paper) items[baseOf(key) + ':paper'] = paper;
            post(items, []);
        } else if (kindOf(key) === 'paper' && isFlagged(baseOf(key))) {
            items[key] = String(value);
            post(items, []);
        }
    };

    proto.removeItem = function(key) {
        rawRemoveItem.call(this, key);
        if (CAN_WRITE && this === storage && typeof key === 'string' && isFlagKey(key)) post({}, [key]);
    };

    // --- Legacy historical report takeover (.craft-save-btn -> Save to Obsidian) ---

    function detectReportDate() {
        if (typeof window.REPORT_DATE === 'string' && window.REPORT_DATE) {
            return window.REPORT_DATE;
        }
        var m = (window.location.pathname || '').match(/(\d{4}-\d{2}-\d{2})/);
        if (m) return m[1];
        var titleMatch = (document.title || '').match(/(\d{4}-\d{2}-\d{2})/);
        return titleMatch ? titleMatch[1] : '';
    }

    function scrapeField(item, label) {
        var paras = item.querySelectorAll('p');
        for (var i = 0; i < paras.length; i += 1) {
            var strong = paras[i].querySelector('strong');
            if (strong && strong.textContent.trim() === label) {
                return paras[i].textContent.slice(strong.textContent.length).trim();
            }
        }
        return '';
    }

    function scrapeStatus(item) {
        var tag = item.querySelector('.status-tag');
        if (!tag) return 'submitted';
        if (tag.classList.contains('status-accepted')) return 'accepted';
        if (tag.classList.contains('status-published')) return 'published';
        return 'submitted';
    }

    function scrapeMethodTag(item) {
        var tag = item.querySelector('.method-tag');
        return tag ? tag.textContent.trim().replace(/^\[(.*)\]$/, '$1') : '';
    }

    function scrapeCardData(item) {
        var link = item.querySelector('h3 a');
        return {
            url: link ? link.href : '',
            arxivId: link ? link.textContent.trim() : '',
            titleEn: scrapeField(item, '英文标题：'),
            titleZh: scrapeField(item, '中文标题：'),
            authors: scrapeField(item, '作者：'),
            question: scrapeField(item, '研究问题：'),
            method: scrapeField(item, '研究方法：'),
            methodTag: scrapeMethodTag(item),
            result: scrapeField(item, '研究结果：'),
            caveat: scrapeField(item, '潜在不足与延伸方向：'),
            status: scrapeStatus(item),
            reportDate: detectReportDate()
        };
    }

    function cardStateBase(item, data) {
        var id = data.arxivId || item.id || 'paper';
        return PREFIX + data.reportDate + ':' + id;
    }

    function isCardSaved(base, arxivId) {
        if (storage.getItem(base + ':obsidian') === '1' || storage.getItem(base + ':crafted') === '1') {
            return true;
        }
        if (!arxivId || storage.getItem(UNMARKED_PREFIX + arxivId) === '1') return false;
        var ids = Array.isArray(window.__OBSIDIAN_SAVED_IDS__) ? window.__OBSIDIAN_SAVED_IDS__ : [];
        return ids.indexOf(arxivId) !== -1;
    }

    function syncLegacyCard(item) {
        var btn = item.querySelector('.craft-save-btn');
        if (!btn) return;
        var data = scrapeCardData(item);
        var base = cardStateBase(item, data);
        var saved = isCardSaved(base, data.arxivId);
        item.dataset.crafted = saved ? 'obsidian' : '';
        item.classList.toggle('is-crafted', saved);
        btn.classList.toggle('is-active', saved);
        btn.title = saved ? 'Remove Obsidian mark' : 'Save to Obsidian';
        btn.setAttribute('aria-label', btn.title);
        var indexLink = document.querySelector('#paper-mini-index a[data-target-id="' + item.id + '"]');
        if (indexLink) indexLink.classList.toggle('is-crafted', saved);
    }

    function extractMethodParts(method, methodTag) {
        var rawMethod = String(method || '').trim();
        var rawTag = String(methodTag || '').trim().replace(/^\[|\]$/g, '').trim();
        var m = rawMethod.match(/^\s*\[([A-Za-z][A-Za-z0-9_-]*)\]\s*/);
        if (m) {
            var extracted = m[1];
            var rest = rawMethod.slice(m[0].length).trim();
            var dup = rest.match(/^\s*\[([A-Za-z][A-Za-z0-9_-]*)\]\s*/);
            if (dup && dup[1].toLowerCase() === extracted.toLowerCase()) {
                rest = rest.slice(dup[0].length).trim();
            }
            var finalTag = rawTag || extracted;
            return {tag: finalTag, body: rest ? '[' + finalTag + '] ' + rest : '[' + finalTag + ']'};
        }
        if (rawTag && rawMethod) return {tag: rawTag, body: '[' + rawTag + '] ' + rawMethod};
        return {tag: rawTag, body: rawMethod};
    }

    function formatObsidianMarkdown(data, note) {
        var arxivId = String(data.arxivId || '').trim();
        var url = String(data.url || '').trim();
        var titleEn = String(data.titleEn || '').trim() || 'Unknown';
        var authorsRaw = String(data.authors || '').trim();
        var methodParts = extractMethodParts(data.method, data.methodTag);
        var noteText = String(note || '').trim();

        var lines = [
            titleEn,
            '',
            '[arXiv:' + (arxivId || 'Unknown') + '](' + url + ')',
            '',
            '**作者：** ' + (authorsRaw || 'Unknown'),
            ''
        ];
        var analysis = [];
        if (data.question) analysis.push('**研究问题：** ' + data.question);
        if (methodParts.body) analysis.push('**研究方法：** ' + methodParts.body);
        if (data.result) analysis.push('**研究结果：** ' + data.result);
        if (data.caveat) analysis.push('**潜在不足与延伸方向：** ' + data.caveat);
        if (analysis.length) lines.push(analysis.join('\n'));
        if (noteText) lines.push('', '---', '', '**个人备注：** ' + noteText);
        return lines.join('\n') + '\n';
    }

    function copyWithFeedback(text, btn) {
        var finish = function() {
            var old = btn.innerHTML;
            btn.textContent = '✓';
            setTimeout(function() { btn.innerHTML = old; }, 1600);
        };
        var fallback = function() {
            var area = document.createElement('textarea');
            area.value = text;
            area.setAttribute('readonly', '');
            area.style.position = 'fixed';
            area.style.left = '-9999px';
            document.body.appendChild(area);
            area.select();
            document.execCommand('copy');
            area.remove();
            finish();
        };
        if (navigator.clipboard && window.isSecureContext) {
            navigator.clipboard.writeText(text).then(finish).catch(fallback);
        } else {
            fallback();
        }
    }

    function injectLegacyObsidianStyles() {
        if (document.getElementById('obsidian-legacy-override')) return;
        var style = document.createElement('style');
        style.id = 'obsidian-legacy-override';
        style.textContent = ':root { --craft-bg: #f5f3ff !important; --craft-fg: #6d28d9 !important; --craft-border: #c4b5fd !important; }\n'
            + '@media (prefers-color-scheme: dark) { :root { --craft-bg: rgba(139, 92, 246, 0.16) !important; --craft-fg: #c4b5fd !important; --craft-border: rgba(196, 181, 253, 0.35) !important; } }';
        (document.head || document.documentElement).appendChild(style);
    }

    function installLegacyTakeover() {
        var legacyButtons = document.querySelectorAll('.craft-save-btn');
        if (!legacyButtons.length) return;
        if (!CAN_WRITE) {
            legacyButtons.forEach(function(btn) { btn.style.display = 'none'; });
            return;
        }
        injectLegacyObsidianStyles();
        document.querySelectorAll('.paper-item').forEach(syncLegacyCard);
        document.addEventListener('paper-state-change', function() {
            document.querySelectorAll('.paper-item').forEach(syncLegacyCard);
        });

        document.addEventListener('click', function(e) {
            var craftBtn = e.target.closest ? e.target.closest('.craft-save-btn') : null;
            if (craftBtn) {
                var item = craftBtn.closest('.paper-item');
                if (!item) return;
                e.preventDefault();
                e.stopImmediatePropagation();
                var data = scrapeCardData(item);
                var base = cardStateBase(item, data);
                var id = String(data.arxivId || '').trim();
                if (isCardSaved(base, id)) {
                    storage.removeItem(base + ':obsidian');
                    storage.removeItem(base + ':crafted');
                    if (id) storage.setItem(UNMARKED_PREFIX + id, '1');
                    syncLegacyCard(item);
                    document.dispatchEvent(new CustomEvent('paper-state-change'));
                    return;
                }
                var note = prompt('Optional personal note:');
                if (note === null) return;
                fetch(OBSIDIAN_SAVE_ENDPOINT, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        arxiv_id: id,
                        url: data.url,
                        title_en: data.titleEn,
                        title_zh: data.titleZh,
                        authors: data.authors,
                        question: data.question,
                        method: data.method,
                        method_tag: data.methodTag,
                        result: data.result,
                        caveat: data.caveat,
                        paper_status: data.status,
                        report_date: data.reportDate,
                        note: note
                    })
                }).then(function(resp) {
                    if (!resp.ok) throw new Error('HTTP ' + resp.status);
                    return resp.json();
                }).then(function() {
                    if (id) {
                        storage.removeItem(UNMARKED_PREFIX + id);
                        if (Array.isArray(window.__OBSIDIAN_SAVED_IDS__) && window.__OBSIDIAN_SAVED_IDS__.indexOf(id) === -1) {
                            window.__OBSIDIAN_SAVED_IDS__.push(id);
                        }
                    }
                    storage.setItem(base + ':obsidian', '1');
                    syncLegacyCard(item);
                    document.dispatchEvent(new CustomEvent('paper-state-change'));
                    var oldHtml = craftBtn.innerHTML;
                    craftBtn.textContent = '✓';
                    craftBtn.title = 'Saved to Obsidian';
                    setTimeout(function() {
                        craftBtn.innerHTML = oldHtml;
                        syncLegacyCard(item);
                    }, 1600);
                }).catch(function(err) {
                    console.error('Failed to save Obsidian note', err);
                    alert('Failed to save to Obsidian: ' + err.message);
                });
                return;
            }

            var copyBtn = e.target.closest ? e.target.closest('.paper-action-btn[title="Copy Markdown"]') : null;
            if (copyBtn) {
                var card = copyBtn.closest('.paper-item');
                if (!card || !card.querySelector('.craft-save-btn')) return;
                e.preventDefault();
                e.stopImmediatePropagation();
                var copyNote = prompt('Optional personal note:');
                if (copyNote === null) return;
                copyWithFeedback(formatObsidianMarkdown(scrapeCardData(card), copyNote), copyBtn);
            }
        }, true);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', installLegacyTakeover);
    } else {
        installLegacyTakeover();
    }
})();
