'use strict';

const fs = require('fs');
const path = require('path');
const { app } = require('electron');

const FILE = () => path.join(app.getPath('userData'), 'sessions.json');
const MAX_SESSIONS = 100;

let cache = null;

function loadAll() {
  if (cache) return cache;
  try {
    cache = JSON.parse(fs.readFileSync(FILE(), 'utf8'));
    if (!Array.isArray(cache)) cache = [];
  } catch {
    cache = [];
  }
  return cache;
}

function persist() {
  try {
    fs.mkdirSync(path.dirname(FILE()), { recursive: true });
    fs.writeFileSync(FILE(), JSON.stringify(cache.slice(0, MAX_SESSIONS), null, 2), 'utf8');
  } catch (err) {
    console.error('Gecmis kaydedilemedi:', err.message);
  }
}

function list() {
  return loadAll().map((s) => ({ id: s.id, title: s.title, updatedAt: s.updatedAt }));
}

function get(id) {
  return loadAll().find((s) => s.id === id) || null;
}

function save(session) {
  const all = loadAll();
  const idx = all.findIndex((s) => s.id === session.id);
  const record = { ...session, updatedAt: Date.now() };
  if (idx >= 0) all[idx] = record;
  else all.unshift(record);

  // En son guncellenen en ustte
  all.sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));
  cache = all.slice(0, MAX_SESSIONS);
  persist();
  return record;
}

function remove(id) {
  cache = loadAll().filter((s) => s.id !== id);
  persist();
}

function clear() {
  cache = [];
  persist();
}

function makeTitle(text) {
  const t = String(text || '').replace(/\s+/g, ' ').trim();
  if (!t) return 'Yeni sohbet';
  return t.length > 48 ? t.slice(0, 48) + '…' : t;
}

module.exports = { list, get, save, remove, clear, makeTitle };
