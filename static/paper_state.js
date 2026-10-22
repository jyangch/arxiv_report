// Mirror starred / crafted paper state between localStorage and the server.
//
// Injected by server.py at the top of every report and starred page, so it
// runs before the page's own scripts. It pulls /api/paper-state into
// localStorage synchronously (the pages read localStorage on load), then
// forwards every later write of an arxiv-report:* flag to the server.
(function() {
    var PREFIX = 'arxiv-report:';
    var MIGRATED_KEY = 'arxiv-report-sync:migrated';
    var ENDPOINT = '/api/paper-state';

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
        return key.indexOf(PREFIX) === 0 && (kind === 'starred' || kind === 'crafted');
    }

    function isFlagged(base) {
        return storage.getItem(base + ':starred') === '1'
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

    function pullServerState() {
        var xhr = new XMLHttpRequest();
        xhr.open('GET', ENDPOINT, false);
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
    try {
        server = pullServerState();
    } catch (err) {
        console.warn('paper state pull failed; using browser state only', err);
    }

    if (server) {
        // The first sync in a browser uploads its existing flags; after that
        // the server is authoritative, so flags it no longer has are dropped.
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
        if (this !== storage || typeof key !== 'string' || key.indexOf(PREFIX) !== 0) return;
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
        if (this === storage && typeof key === 'string' && isFlagKey(key)) post({}, [key]);
    };
})();
