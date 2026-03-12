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
import threading
import time
import urllib.request
import urllib.parse

# ── Config ──────────────────────────────────────────────────────────────────
PORT     = 8888
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
os.makedirs(DATA_DIR, exist_ok=True)

app      = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading')

# ── RocketShow helpers ────────────────────────────────────────────────────────
def rs_get_host():
    s = load_json('settings.json', {'rs_host': 'rocketshow.local', 'rs_port': '80'})
    return s.get('rs_host', 'rocketshow.local'), str(s.get('rs_port', '80'))

def rs_fetch(path):
    """GET vers RocketShow. Essaie l'hôte configuré puis localhost en fallback."""
    host, port = rs_get_host()
    url = f'http://{host}:{port}{path}'
    try:
        req = urllib.request.Request(url, method='GET')
        with urllib.request.urlopen(req, timeout=2) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        if host in ('localhost', '127.0.0.1'):
            raise
        # Fallback localhost : RocketShow tourne sur le même Pi que ShowMaster+
        url2 = f'http://localhost:{port}{path}'
        req2 = urllib.request.Request(url2, method='GET')
        with urllib.request.urlopen(req2, timeout=2) as resp:
            return json.loads(resp.read().decode())

def rs_post(path):
    """POST vers RocketShow. Essaie l'hôte configuré puis localhost en fallback."""
    host, port = rs_get_host()
    url = f'http://{host}:{port}{path}'
    try:
        req = urllib.request.Request(url, data=b'', method='POST')
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status
    except Exception:
        if host in ('localhost', '127.0.0.1'):
            raise
        # Fallback localhost
        url2 = f'http://localhost:{port}{path}'
        req2 = urllib.request.Request(url2, data=b'', method='POST')
        with urllib.request.urlopen(req2, timeout=3) as resp:
            return resp.status

def rs_load_and_play(name):
    """Charge une composition dans RS puis la lance."""
    try:
        encoded = urllib.parse.quote(name)
        rs_post(f'/api/transport/set-composition-name?name={encoded}')
    except Exception as e:
        print(f'[RS] set-composition-name error: {e}')
        return
    try:
        rs_post('/api/transport/play')
    except Exception as e:
        print(f'[RS] play error: {e}')

def rs_load_only(name):
    """Pré-charge une composition dans RS sans la lancer (mode manuel)."""
    try:
        encoded = urllib.parse.quote(name)
        rs_post(f'/api/transport/set-composition-name?name={encoded}')
    except Exception as e:
        print(f'[RS] preload error: {e}')

# ── Boucle RS côté serveur (auto-play, indépendant du navigateur) ─────────────
_rs_prev_state   = 'STOPPED'
_rs_prev_pos_ms  = 0
_rs_prev_dur_ms  = 0
_rs_last_error   = ''
_rs_last_data    = {}
_rs_poll_count   = 0
_rs_play_started = 0   # timestamp (time.time()) quand RS est passé en PLAYING

def rs_auto_loop():
    global _rs_prev_state, _rs_prev_pos_ms, _rs_prev_dur_ms
    global _rs_last_error, _rs_last_data, _rs_poll_count
    global _rs_play_started
    print('[RS] Boucle auto-play démarrée')
    while True:
        try:
            data   = rs_fetch('/api/system/state')
            _rs_last_data  = data
            _rs_last_error = ''
            _rs_poll_count += 1
            ps     = (data.get('playState') or 'STOPPED').upper()
            pos_ms = data.get('positionMillis') or 0
            dur_ms = data.get('currentCompositionDurationMillis') or 0

            # Suivre quand RS commence à jouer (pour le fallback sans durée)
            if ps == 'PLAYING' and _rs_prev_state != 'PLAYING':
                _rs_play_started = time.time()

            # Détection fin de chanson : RS passe de PLAYING à STOPPED en fin naturelle
            if ps == 'STOPPED' and _rs_prev_state == 'PLAYING':
                play_secs = time.time() - _rs_play_started if _rs_play_started else 0
                # Critère principal : position atteint 88% de la durée connue
                near_end_by_pos = _rs_prev_dur_ms > 0 and _rs_prev_pos_ms >= _rs_prev_dur_ms * 0.88
                # Fallback : durée inconnue (RS ne renvoie pas currentCompositionDurationMillis)
                # mais on a joué au moins 10 secondes → on suppose fin naturelle
                near_end_by_time = _rs_prev_dur_ms == 0 and play_secs >= 10
                near_end = near_end_by_pos or near_end_by_time
                print(f'[RS] PLAYING→STOPPED | pos={_rs_prev_pos_ms}ms dur={_rs_prev_dur_ms}ms '
                      f'play_secs={play_secs:.1f} near_end={near_end}')
                if near_end:
                    state       = load_json('state.json', STATE_DEFAULT)
                    q           = state.get('queue', [])
                    auto_mode   = state.get('autoMode', True)
                    now_playing = state.get('nowPlaying')

                    if now_playing and q:
                        next_song = q.pop(0)
                        state['queue']      = q
                        state['nowPlaying'] = next_song

                        if auto_mode:
                            state['isPlaying'] = True
                            save_json('state.json', state)
                            socketio.emit('state_update', state, namespace='/')
                            rs_name = next_song.get('rsName') or next_song.get('title', '')
                            print(f'[RS] Auto-play → "{rs_name}"')
                            threading.Timer(0.4, rs_load_and_play, args=[rs_name]).start()
                        else:
                            # Mode manuel : pré-charger mais ne pas jouer
                            state['isPlaying'] = False
                            save_json('state.json', state)
                            socketio.emit('state_update', state, namespace='/')
                            rs_name = next_song.get('rsName') or next_song.get('title', '')
                            print(f'[RS] Mode manuel : pré-charge "{rs_name}"')
                            threading.Thread(target=rs_load_only, args=[rs_name], daemon=True).start()

                    elif now_playing and not q:
                        # File vide — fin du show
                        state['nowPlaying'] = None
                        state['isPlaying']  = False
                        save_json('state.json', state)
                        socketio.emit('state_update', state, namespace='/')
                        print('[RS] Fin de la file d\'attente')

            _rs_prev_state = ps
            if pos_ms > 0: _rs_prev_pos_ms = pos_ms
            if dur_ms > 0: _rs_prev_dur_ms = dur_ms

        except Exception as e:
            _rs_last_error = str(e)
            _rs_poll_count += 1
            print(f'[RS] Poll error: {e}')

        time.sleep(1)

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

@app.route('/lyrics')
def prompteur():
    return send_from_directory(BASE_DIR, 'prompteur.html')

# ── STATE : file d'attente + titre en cours ───────────────────────────────────
STATE_DEFAULT = {
    'queue': [],
    'nowPlaying': None,
    'isPlaying': False,
    'nowPlayingLocked': False,
    'autoMode': True
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

# ── SETTINGS : config RocketShow partagée ─────────────────────────────────────
SETTINGS_DEFAULT = {'rs_host': 'rocketshow.local', 'rs_port': '80'}

@app.route('/api/settings', methods=['GET'])
def get_settings():
    return jsonify(load_json('settings.json', SETTINGS_DEFAULT))

@app.route('/api/settings', methods=['POST'])
def post_settings():
    data = request.get_json(force=True)
    save_json('settings.json', data)
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

# ── Démarrer la boucle RS (indépendant du mode de lancement) ─────────────────
_rs_thread = threading.Thread(target=rs_auto_loop, daemon=True)
_rs_thread.start()

# ── Endpoints debug RS ─────────────────────────────────────────────────────────
@app.route('/api/rs/debug')
def rs_debug():
    host, port = rs_get_host()
    return jsonify({
        'thread_alive':  _rs_thread.is_alive(),
        'poll_count':    _rs_poll_count,
        'prev_state':    _rs_prev_state,
        'prev_pos_ms':   _rs_prev_pos_ms,
        'prev_dur_ms':   _rs_prev_dur_ms,
        'play_secs_ago': round(time.time() - _rs_play_started, 1) if _rs_play_started else None,
        'last_error':    _rs_last_error,
        'last_data':     _rs_last_data,
        'rs_host':       host,
        'rs_port':       port,
    })

@app.route('/api/rs/test')
def rs_test():
    """Test immédiat de la connexion au RocketShow depuis le serveur."""
    host, port = rs_get_host()
    try:
        data = rs_fetch('/api/system/state')
        return jsonify({'ok': True, 'host': host, 'port': port, 'data': data})
    except Exception as e:
        # Essai fallback sur localhost si l'hôte configuré échoue
        fallback_result = None
        if host != 'localhost' and host != '127.0.0.1':
            try:
                url = f'http://localhost:{port}/api/system/state'
                req = urllib.request.Request(url, method='GET')
                with urllib.request.urlopen(req, timeout=2) as resp:
                    fallback_result = json.loads(resp.read().decode())
            except Exception as e2:
                fallback_result = f'aussi échoué: {e2}'
        return jsonify({
            'ok': False,
            'host': host,
            'port': port,
            'error': str(e),
            'localhost_fallback': fallback_result,
        })

# ── Lancement ──────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print(f'ShowMaster+ démarré sur http://0.0.0.0:{PORT}')
    print(f'Données stockées dans : {DATA_DIR}')
    socketio.run(app, host='0.0.0.0', port=PORT, debug=False, allow_unsafe_werkzeug=True)
