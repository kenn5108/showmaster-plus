#!/usr/bin/env python3
"""
ShowMaster+ Server
Sert l'interface HTML, persiste les données en JSON,
et synchronise en temps réel tous les appareils connectés via WebSocket.

Lancement : python3 showmaster.py
Accès      : http://[IP-DU-PI]:8888
"""

from flask import Flask, jsonify, request, send_from_directory
from flask_socketio import SocketIO, emit
import json
import os

# ── Config ──────────────────────────────────────────────────────────────────
PORT     = 8888
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
os.makedirs(DATA_DIR, exist_ok=True)

app      = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading')

# ── Helpers JSON ─────────────────────────────────────────────────────────────
def load_json(filename, default):
    path = os.path.join(DATA_DIR, filename)
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return default

def save_json(filename, data):
    path = os.path.join(DATA_DIR, filename)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ── Servir le HTML ────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory(BASE_DIR, 'prototype-v2.html')

# ── STATE : file d'attente + titre en cours ───────────────────────────────────
STATE_DEFAULT = {
    'queue': [],
    'nowPlaying': None,
    'isPlaying': False,
    'nowPlayingLocked': False
}

@app.route('/api/state', methods=['GET'])
def get_state():
    return jsonify(load_json('state.json', STATE_DEFAULT))

@app.route('/api/state', methods=['POST'])
def post_state():
    data = request.get_json(force=True)
    save_json('state.json', data)
    # Diffuse à tous les autres clients connectés
    socketio.emit('state_update', data, skip_sid=request.headers.get('X-Socket-Id'))
    return jsonify({'ok': True})

# ── SONGS : catalogue + statuts ───────────────────────────────────────────────
SONGS_DEFAULT = {'songs': [], 'statuses': {}}

@app.route('/api/songs', methods=['GET'])
def get_songs():
    return jsonify(load_json('songs.json', SONGS_DEFAULT))

@app.route('/api/songs', methods=['POST'])
def post_songs():
    data = request.get_json(force=True)
    save_json('songs.json', data)
    return jsonify({'ok': True})

# ── LYRICS : synchronisations paroles ────────────────────────────────────────
@app.route('/api/lyrics', methods=['GET'])
def get_lyrics():
    return jsonify(load_json('lyrics.json', {}))

@app.route('/api/lyrics', methods=['POST'])
def post_lyrics():
    data = request.get_json(force=True)
    save_json('lyrics.json', data)
    return jsonify({'ok': True})

# ── PLAYLISTS ──────────────────────────────────────────────────────────────────
@app.route('/api/playlists', methods=['GET'])
def get_playlists():
    return jsonify(load_json('playlists.json', {}))

@app.route('/api/playlists', methods=['POST'])
def post_playlists():
    data = request.get_json(force=True)
    save_json('playlists.json', data)
    return jsonify({'ok': True})

# ── WebSocket ──────────────────────────────────────────────────────────────────
@socketio.on('connect')
def on_connect():
    # Envoie l'état courant au client qui vient de se connecter
    state = load_json('state.json', STATE_DEFAULT)
    emit('state_update', state)
    print(f'[WS] Client connecté : {request.sid}')

@socketio.on('disconnect')
def on_disconnect():
    print(f'[WS] Client déconnecté : {request.sid}')

# ── Lancement ──────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print(f'ShowMaster+ démarré sur http://0.0.0.0:{PORT}')
    print(f'Données stockées dans : {DATA_DIR}')
    socketio.run(app, host='0.0.0.0', port=PORT, debug=False)
