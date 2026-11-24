#!/usr/bin/env python3
"""Build test/menu_test.html: menu/menu.html with in-memory stand-ins for claude.use("db" | "user" | "downloads").

    python3 test/build_menu_test.py
    python3 -m http.server 8767 --directory test

Open http://localhost:8767/menu_test.html?as=owner#owner (owner console) or ?as=guest#table-4 (a guest).
Everything is held in memory and resets on reload. For testing only; never publish the built file."""
import os
HERE = os.path.dirname(os.path.abspath(__file__))
STUB = r'''<style>[hidden]{display:none!important}body{margin:0}</style><script>
(() => {
  const store = window.__store = {}, subs = [], errors = window.__errors = []; window.__saved = [];
  window.addEventListener('error', e => errors.push(String(e.message)));
  const coll = p => p.split('/').slice(0, -1).join('/');
  const snapDoc = p => ({ id: p.split('/').pop(), exists: p in store, data: () => store[p] && JSON.parse(JSON.stringify(store[p])) });
  const fire = () => setTimeout(() => subs.forEach(s => s()), 0);
  const doc = p => ({ id: p.split('/').pop(), path: p, get: async () => snapDoc(p),
    set: async d => { store[p] = JSON.parse(JSON.stringify(d)); fire(); },
    update: async d => { if (!(p in store)) throw { code: 'not_found' }; Object.assign(store[p], JSON.parse(JSON.stringify(d))); fire(); },
    delete: async () => { delete store[p]; fire(); },
    onSnapshot: next => { const f = () => next(snapDoc(p)); subs.push(f); f(); return () => {}; } });
  const collection = (c, filt) => { const docs = () => Object.keys(store).filter(k => coll(k) === c && (!filt || filt(store[k]))).map(snapDoc);
    const q = { path: c, doc: id => doc(c + '/' + (id || Math.random().toString(36).slice(2))),
      add: async d => { const r = doc(c + '/' + Math.random().toString(36).slice(2)); await r.set(d); return r; },
      orderBy: () => q, limit: () => q,
      where: (f, op, v) => collection(c, d => op === '>=' ? String(d[f]) >= v : d[f] === v),
      get: async () => ({ docs: docs() }), onSnapshot: next => { const f = () => next({ docs: docs() }); subs.push(f); f(); return () => {}; } }; return q; };
  const role = new URLSearchParams(location.search).get('as') || 'owner';
  const user = { id: async () => role === 'owner' ? 'u_owner' : 'u_guest', canEdit: async () => role === 'owner', isOwner: async () => role === 'owner' };
  const downloads = { save: async r => { window.__saved.push(r); return { saved: true }; } };
  window.claude = { use: async n => (n === 'db' ? { doc, collection } : n === 'user' ? user : n === 'downloads' ? downloads : null) };
})();
</script>'''
page = open(os.path.join(HERE, "..", "menu", "menu.html")).read()
open(os.path.join(HERE, "menu_test.html"), "w").write('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">' + STUB + page)
print("built test/menu_test.html")
