"""
Infinite Directive Build Service — Flask Backend
Handles customer form submissions, EVE SSO auth, and Discord notifications.
"""
import os
import json
import secrets
import sqlite3
import hashlib
from base64 import b64encode
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests
from flask import (Flask, g, redirect, render_template, request,
                    session, url_for, jsonify, abort)

# ── Paths ──────────────────────────────────────────────────────────────────
APP_DIR     = os.path.dirname(os.path.abspath(__file__))
DB_PATH     = os.path.join(APP_DIR, 'mydatabase.db')
CREDS_PATH  = os.path.join(APP_DIR, 'config', 'credentials.json')

# ── ESI / EVE constants ────────────────────────────────────────────────────
ESI_BASE        = 'https://esi.evetech.net/latest'
EVE_OAUTH_URL   = 'https://login.eveonline.com/v2/oauth/authorize'
EVE_TOKEN_URL   = 'https://login.eveonline.com/v2/oauth/token'
EVE_VERIFY_URL  = 'https://esi.evetech.net/verify/'
EVE_SCOPE       = 'publicData'

# ── App ────────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET', secrets.token_hex(32))


# ── DB helpers ─────────────────────────────────────────────────────────────
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop('db', None)
    if db:
        db.close()


def cfg(key, default=''):
    """Read a value from site_config."""
    row = get_db().execute(
        'SELECT value FROM site_config WHERE key = ?', (key,)
    ).fetchone()
    return row['value'] if row else default


def load_credentials():
    with open(CREDS_PATH) as f:
        return json.load(f)


# ── Auth helpers ───────────────────────────────────────────────────────────
def current_char():
    """Return (character_id, character_name, corp_name) from session."""
    return (
        session.get('character_id'),
        session.get('character_name'),
        session.get('corp_name'),
    )


def verify_alliance(character_id):
    """
    Check alliance membership via public ESI.
    Returns (corp_name, True) on success, (None, False) on failure.
    """
    alliance_id = int(cfg('build_alliance_id', '498125261'))
    try:
        r = requests.get(f'{ESI_BASE}/characters/{character_id}/', timeout=10)
        if r.status_code != 200:
            return None, False
        corp_id = r.json().get('corporation_id')

        r2 = requests.get(f'{ESI_BASE}/corporations/{corp_id}/', timeout=10)
        if r2.status_code != 200:
            return None, False
        corp_data = r2.json()

        if corp_data.get('alliance_id') != alliance_id:
            return None, False

        return corp_data.get('name', ''), True
    except Exception:
        return None, False


# ── Discord helper ─────────────────────────────────────────────────────────
def send_discord_notification(req_id, item_name, qty, customer_name, lookup_token):
    webhook = cfg('build_discord_webhook')
    if not webhook:
        return
    content = (
        f'**New Build Request — REQ-{req_id:04d}**\n'
        f'Item: **{item_name}** ×{qty}\n'
        f'Customer: {customer_name}\n'
        f'Lookup: `{lookup_token}`\n'
        f'Status: pending quote'
    )
    try:
        requests.post(webhook, json={'content': content}, timeout=8)
    except Exception:
        pass


# ── Item search API ────────────────────────────────────────────────────────
@app.get('/api/items')
def api_items():
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify([])
    db = get_db()
    rows = db.execute(
        """SELECT t.type_id, t.type_name, g.group_name
            FROM inv_types t
            LEFT JOIN inv_groups g ON t.group_id = g.group_id
            WHERE t.type_name LIKE ? AND t.published = 1
            LIMIT 30""",
        (f'%{q}%',)
    ).fetchall()
    return jsonify([
        {'id': r['type_id'], 'name': r['type_name'], 'group': r['group_name'] or ''}
        for r in rows
    ])


# ── Doctrine fits API ──────────────────────────────────────────────────────
@app.get('/api/doctrine-fits')
def api_doctrine_fits():
    db = get_db()
    rows = db.execute(
        'SELECT id, fit_name, ship_name, ship_class FROM doctrine_fits ORDER BY ship_class, fit_name'
    ).fetchall()
    return jsonify([dict(r) for r in rows])


# ── EVE SSO ───────────────────────────────────────────────────────────────
@app.get('/auth/login')
def auth_login():
    creds = load_credentials()
    state = secrets.token_urlsafe(16)
    session['oauth_state'] = state
    session['oauth_next'] = request.args.get('next', '/')

    params = urlencode({
        'response_type': 'code',
        'redirect_uri':  cfg('build_redirect_uri', 'http://localhost:5000/auth/callback'),
        'client_id':     creds['client_id'],
        'scope':         EVE_SCOPE,
        'state':         state,
    })
    return redirect(f'{EVE_OAUTH_URL}?{params}')


@app.get('/auth/callback')
def auth_callback():
    # Validate state
    if request.args.get('state') != session.pop('oauth_state', None):
        abort(400, 'Invalid OAuth state')

    code = request.args.get('code')
    if not code:
        abort(400, 'No code returned from EVE SSO')

    # Exchange code for token
    creds = load_credentials()
    auth_str = b64encode(
        f"{creds['client_id']}:{creds['client_secret']}".encode()
    ).decode()

    r = requests.post(
        EVE_TOKEN_URL,
        headers={
            'Authorization': f'Basic {auth_str}',
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        data={'grant_type': 'authorization_code', 'code': code},
        timeout=15,
    )
    if r.status_code != 200:
        abort(502, 'EVE SSO token exchange failed')

    token_data = r.json()
    access_token = token_data['access_token']

    # Verify token and get character info
    v = requests.get(
        EVE_VERIFY_URL,
        headers={'Authorization': f'Bearer {access_token}'},
        timeout=10,
    )
    if v.status_code != 200:
        abort(502, 'EVE token verification failed')

    char_data = v.json()
    character_id   = char_data['CharacterID']
    character_name = char_data['CharacterName']

    # Verify alliance membership
    corp_name, in_alliance = verify_alliance(character_id)
    if not in_alliance:
        session.clear()
        return render_template(
            'bs_error.html',
            title='Access Denied',
            message='You must be a member of TEST Alliance Please Ignore to use this service.',
        ), 403

    session['character_id']   = character_id
    session['character_name'] = character_name
    session['corp_name']      = corp_name

    next_url = session.pop('oauth_next', '/')
    return redirect(next_url)


@app.get('/auth/logout')
def auth_logout():
    session.clear()
    return redirect('/')


# ── Customer request form ──────────────────────────────────────────────────
@app.get('/')
def index():
    char_id, char_name, corp_name = current_char()
    return render_template('bs_form.html', character_name=char_name, corp_name=corp_name)


@app.post('/request')
def submit_request():
    char_id, char_name, corp_name = current_char()

    item_name  = request.form.get('item_name', '').strip()
    item_type_id = request.form.get('item_type_id') or None
    quantity   = max(1, int(request.form.get('quantity', 1) or 1))
    is_fit     = request.form.get('is_doctrine_fit') == '1'
    fit_id     = request.form.get('doctrine_fit_id') or None
    delivery   = request.form.get('delivery_location', '').strip() or cfg('build_delivery_default')
    deadline   = request.form.get('deadline') or None
    notes      = request.form.get('notes', '').strip() or None
    cust_name  = char_name or request.form.get('customer_name', '').strip()

    if not item_name or not cust_name:
        return render_template(
            'bs_form.html',
            character_name=char_name,
            error='Item name and character name are required.'
        ), 400

    # Generate lookup token: 4+4+4 formatted like REQ-XXXX-YYYY
    token = secrets.token_hex(6).upper()
    lookup_token = f'{token[:4]}-{token[4:8]}-{token[8:]}'

    db = get_db()
    cur = db.execute(
        """INSERT INTO build_requests
            (status, lookup_token, customer_name, character_id, character_name,
            item_type_id, item_name, quantity, is_doctrine_fit, doctrine_fit_id,
            delivery_location, deadline, notes, markup_pct)
            VALUES ('pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    CAST((SELECT value FROM site_config WHERE key='build_default_markup_pct') AS REAL))""",
        (lookup_token, cust_name, char_id, char_name,
        item_type_id, item_name, quantity,
        1 if is_fit else 0, fit_id,
        delivery, deadline, notes)
    )
    req_id = cur.lastrowid

    # If doctrine fit, insert line items
    if is_fit and fit_id:
        items = db.execute(
            'SELECT type_id, item_name, quantity FROM doctrine_fit_items WHERE fit_id = ?',
            (fit_id,)
        ).fetchall()
        for it in items:
            db.execute(
                'INSERT INTO build_request_items (request_id, type_id, item_name, quantity) VALUES (?, ?, ?, ?)',
                (req_id, it['type_id'], it['item_name'], it['quantity'] * quantity)
            )

    db.commit()

    # Discord notification
    send_discord_notification(req_id, item_name, quantity, cust_name, lookup_token)

    return redirect(url_for('status_page', token=lookup_token))


# ── Status lookup ──────────────────────────────────────────────────────────
@app.get('/status')
def status_index():
    char_id, char_name, corp_name = current_char()
    token = request.args.get('token', '').strip().upper()
    req = None
    error = None

    if token:
        req = get_db().execute(
            'SELECT * FROM build_requests WHERE lookup_token = ?', (token,)
        ).fetchone()
        if not req:
            error = f'No request found for token {token}'

    return render_template(
        'bs_status.html',
        character_name=char_name,
        corp_name=corp_name,
        token=token,
        req=req,
        error=error,
    )


@app.get('/status/<token>')
def status_page(token):
    char_id, char_name, corp_name = current_char()
    token = token.upper()
    req = get_db().execute(
        'SELECT * FROM build_requests WHERE lookup_token = ?', (token,)
    ).fetchone()
    if not req:
        return render_template(
            'bs_error.html',
            title='Request Not Found',
            message=f'No request found with token {token}. Check the token and try again.',
        ), 404
    return render_template('bs_status.html', character_name=char_name, corp_name=corp_name, token=token, req=req, error=None)


# ── Health check ───────────────────────────────────────────────────────────
@app.get('/api/health')
def health():
    return jsonify({'status': 'ok', 'time': datetime.now(timezone.utc).isoformat()})


# ── Run ────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    app.run(debug=True, port=5000)
