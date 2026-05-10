"""
Sky Eduworld — Management System (UPGRADED)
Backend: Flask + PostgreSQL

NEW FEATURES:
- Granular per-user permissions system
- User-wise data isolation (users see only their own students)
- University assignment per user
- Document/Photo file upload system
- Session control (Super Admin can force-logout users)
- User-wise dashboard + combined Super Admin dashboard
- Balance/Payment system per user
"""

import os, csv, io, hashlib, uuid
from datetime import datetime, timedelta
from functools import wraps

from flask import (Flask, request, jsonify, send_from_directory,
                   session, Response, g)
from dotenv import load_dotenv
import psycopg2
import psycopg2.extras

load_dotenv()

app = Flask(__name__, static_folder='static')
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-change-in-prod')
app.permanent_session_lifetime = timedelta(hours=8)

DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://postgres:password@localhost:5432/sky_eduworld')

# Upload folder
UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'pdf', 'doc', 'docx'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ─────────────────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────────────────

def get_db():
    if 'db' not in g:
        g.db = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        g.db.autocommit = False
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def q(sql, params=(), one=False, commit=False):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(sql, params)
    if commit:
        conn.commit()
        return cur.rowcount
    if one:
        row = cur.fetchone()
        return dict(row) if row else None
    rows = cur.fetchall()
    return [dict(r) for r in rows]

def q_returning(sql, params=()):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(sql, params)
    row = cur.fetchone()
    conn.commit()
    return dict(row) if row else None

def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def init_db():
    """Create all tables and seed default data."""
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id           SERIAL PRIMARY KEY,
        username     TEXT UNIQUE NOT NULL,
        password     TEXT NOT NULL,
        full_name    TEXT NOT NULL,
        role         TEXT NOT NULL DEFAULT 'Staff',
        is_active    BOOLEAN DEFAULT TRUE,
        session_token TEXT,
        created_at   TIMESTAMP DEFAULT NOW()
    );

    -- Granular permissions per user
    CREATE TABLE IF NOT EXISTS user_permissions (
        id              SERIAL PRIMARY KEY,
        user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        can_add_student     BOOLEAN DEFAULT TRUE,
        can_edit_student    BOOLEAN DEFAULT TRUE,
        can_delete_student  BOOLEAN DEFAULT FALSE,
        can_view_payments   BOOLEAN DEFAULT TRUE,
        can_add_payment     BOOLEAN DEFAULT TRUE,
        can_view_associates BOOLEAN DEFAULT FALSE,
        can_manage_associates BOOLEAN DEFAULT FALSE,
        can_view_references BOOLEAN DEFAULT FALSE,
        can_manage_references BOOLEAN DEFAULT FALSE,
        can_view_documents  BOOLEAN DEFAULT TRUE,
        can_upload_document BOOLEAN DEFAULT TRUE,
        can_view_reports    BOOLEAN DEFAULT FALSE,
        can_manage_universities BOOLEAN DEFAULT FALSE,
        can_view_all_students   BOOLEAN DEFAULT FALSE,
        UNIQUE(user_id)
    );

    -- Which universities a user can work with (empty = all for Super Admin)
    CREATE TABLE IF NOT EXISTS user_universities (
        id            SERIAL PRIMARY KEY,
        user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        university_id INTEGER NOT NULL REFERENCES universities(id) ON DELETE CASCADE,
        UNIQUE(user_id, university_id)
    );

    CREATE TABLE IF NOT EXISTS universities (
        id    SERIAL PRIMARY KEY,
        name  TEXT UNIQUE NOT NULL,
        state TEXT,
        color TEXT DEFAULT '#1A6CF6'
    );

    CREATE TABLE IF NOT EXISTS students (
        id           SERIAL PRIMARY KEY,
        created_by   INTEGER REFERENCES users(id),
        name         TEXT NOT NULL,
        father       TEXT,
        mother       TEXT,
        dob          DATE,
        gender       TEXT,
        mobile       TEXT,
        email        TEXT,
        aadhar       TEXT,
        address      TEXT,
        course       TEXT,
        university   TEXT,
        batch        TEXT,
        enroll_no    TEXT,
        roll_no      TEXT,
        adm_date     DATE,
        remarks      TEXT,
        total_fee    NUMERIC(12,2) DEFAULT 0,
        paid         NUMERIC(12,2) DEFAULT 0,
        univ_fee     NUMERIC(12,2) DEFAULT 0,
        pay_mode     TEXT,
        utr          TEXT,
        doc_notes    TEXT,
        status       TEXT DEFAULT 'Active',
        photo_path   TEXT,
        created_at   TIMESTAMP DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS fee_payments (
        id          SERIAL PRIMARY KEY,
        student_id  INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
        recorded_by INTEGER REFERENCES users(id),
        amount      NUMERIC(12,2) NOT NULL,
        fee_type    TEXT,
        pay_mode    TEXT,
        ref_no      TEXT,
        pay_date    DATE,
        remarks     TEXT,
        created_at  TIMESTAMP DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS associates (
        id          SERIAL PRIMARY KEY,
        created_by  INTEGER REFERENCES users(id),
        name        TEXT NOT NULL,
        phone       TEXT,
        student     TEXT,
        work_done   TEXT,
        amount      NUMERIC(12,2) DEFAULT 0,
        pay_date    DATE,
        pay_mode    TEXT,
        utr         TEXT,
        status      TEXT DEFAULT 'Paid',
        notes       TEXT,
        created_at  TIMESTAMP DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS references_ (
        id          SERIAL PRIMARY KEY,
        created_by  INTEGER REFERENCES users(id),
        name        TEXT NOT NULL,
        phone       TEXT,
        student     TEXT,
        university  TEXT,
        amount      NUMERIC(12,2) DEFAULT 0,
        pay_date    DATE,
        pay_mode    TEXT,
        utr         TEXT,
        status      TEXT DEFAULT 'Paid',
        notes       TEXT,
        created_at  TIMESTAMP DEFAULT NOW()
    );

    -- Enhanced documents with file upload support
    CREATE TABLE IF NOT EXISTS documents (
        id           SERIAL PRIMARY KEY,
        student_id   INTEGER REFERENCES students(id) ON DELETE SET NULL,
        student      TEXT NOT NULL,
        doc_type     TEXT NOT NULL,
        university   TEXT,
        issue_date   DATE,
        status       TEXT DEFAULT 'Delivered',
        delivered_to TEXT,
        file_path    TEXT,
        file_name    TEXT,
        uploaded_by  INTEGER REFERENCES users(id),
        created_at   TIMESTAMP DEFAULT NOW()
    );

    -- Student photos (separate from documents)
    CREATE TABLE IF NOT EXISTS student_photos (
        id          SERIAL PRIMARY KEY,
        student_id  INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
        file_path   TEXT NOT NULL,
        file_name   TEXT,
        uploaded_by INTEGER REFERENCES users(id),
        uploaded_at TIMESTAMP DEFAULT NOW()
    );
    """)

    # Default admin
    cur.execute("SELECT id FROM users WHERE username='admin'")
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO users (username, password, full_name, role) VALUES (%s, %s, %s, %s) RETURNING id",
            ('admin', hash_pw('sky@2024'), 'Admin', 'Super Admin')
        )

    # Seed universities
    universities = [
        ('Sikkim Alpine University',         'Sikkim',          '#3B82F6'),
        ('Sunrise University',               'Rajasthan',       '#10B981'),
        ('Glocal University',               'Uttar Pradesh',   '#F5A623'),
        ('YBN University',                  'Jharkhand',       '#8B5CF6'),
        ('Nirwan University',               'Rajasthan',       '#F43F5E'),
        ('Manglaytan University',           'Uttar Pradesh',   '#06B6D4'),
        ('Monad University',                'Uttar Pradesh',   '#84CC16'),
        ('Calorax Teachers University',     'Rajasthan',       '#EF4444'),
        ('IEC University',                  'Himachal Pradesh','#F97316'),
        ('Pratap University',               'Rajasthan',       '#EC4899'),
        ('Arni University',                 'Himachal Pradesh','#14B8A6'),
        ('Saroj University',                'Uttar Pradesh',   '#A855F7'),
        ('Shridhar University',             'Rajasthan',       '#0EA5E9'),
        ('Madhyanchal Professional University','Madhya Pradesh','#D97706'),
        ('Mansarovar Global University',    'Madhya Pradesh',  '#7C3AED'),
        ('Mats University',                 'Chhattisgarh',    '#059669'),
        ('North Eastern Christian University','Nagaland',      '#64748B'),
        ('Sabarmati University',            'Gujarat',         '#DC2626'),
        ('P K University',                  'Rajasthan',       '#0891B2'),
    ]
    for u in universities:
        cur.execute(
            "INSERT INTO universities (name, state, color) VALUES (%s, %s, %s) ON CONFLICT (name) DO NOTHING",
            u
        )

    conn.commit()
    conn.close()
    print("✅ Database initialized successfully.")

# ─────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────

def serialize(row):
    out = {}
    for k, v in row.items():
        if hasattr(v, 'isoformat'):
            out[k] = v.isoformat()
        elif hasattr(v, '__float__'):
            out[k] = float(v)
        else:
            out[k] = v
    return out

def is_super_admin():
    return session.get('role') == 'Super Admin'

def is_admin():
    return session.get('role') in ('Admin', 'Super Admin')

def get_user_perms(user_id):
    """Get permissions for a user. Super Admin has all perms."""
    if is_super_admin():
        return {
            'can_add_student': True, 'can_edit_student': True, 'can_delete_student': True,
            'can_view_payments': True, 'can_add_payment': True,
            'can_view_associates': True, 'can_manage_associates': True,
            'can_view_references': True, 'can_manage_references': True,
            'can_view_documents': True, 'can_upload_document': True,
            'can_view_reports': True, 'can_manage_universities': True,
            'can_view_all_students': True,
        }
    perms = q("SELECT * FROM user_permissions WHERE user_id=%s", (user_id,), one=True)
    if not perms:
        # Default perms
        return {
            'can_add_student': True, 'can_edit_student': True, 'can_delete_student': False,
            'can_view_payments': True, 'can_add_payment': True,
            'can_view_associates': False, 'can_manage_associates': False,
            'can_view_references': False, 'can_manage_references': False,
            'can_view_documents': True, 'can_upload_document': True,
            'can_view_reports': False, 'can_manage_universities': False,
            'can_view_all_students': False,
        }
    return dict(perms)

def get_user_universities(user_id):
    """Returns list of university names accessible to user. Empty list = all."""
    if is_super_admin():
        return []  # no restriction
    rows = q("""
        SELECT u.name FROM user_universities uu
        JOIN universities u ON u.id = uu.university_id
        WHERE uu.user_id = %s
    """, (user_id,))
    return [r['name'] for r in rows]

def student_filter_clause(user_id, alias=''):
    """Returns (extra_sql, extra_params) to filter students by user access."""
    prefix = f"{alias}." if alias else ""
    perms = get_user_perms(user_id)
    params = []
    clauses = []

    if not perms.get('can_view_all_students') and not is_super_admin():
        clauses.append(f"{prefix}created_by = %s")
        params.append(user_id)

    univs = get_user_universities(user_id)
    if univs:
        placeholders = ','.join(['%s'] * len(univs))
        clauses.append(f"{prefix}university IN ({placeholders})")
        params.extend(univs)

    sql = (' AND ' + ' AND '.join(clauses)) if clauses else ''
    return sql, params

def require_perm(perm_name):
    """Decorator: checks a named permission."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            perms = get_user_perms(session.get('user_id'))
            if not perms.get(perm_name):
                return jsonify({'error': f'Permission denied: {perm_name}'}), 403
            return f(*args, **kwargs)
        return decorated
    return decorator

# ─────────────────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Not authenticated', 'redirect': '/'}), 401
        # Check session token (for force-logout)
        stored_token = q("SELECT session_token FROM users WHERE id=%s",
                         (session['user_id'],), one=True)
        if stored_token and stored_token.get('session_token') != session.get('session_token'):
            session.clear()
            return jsonify({'error': 'Session expired or revoked', 'redirect': '/'}), 401
        # Check if user is active
        user_active = q("SELECT is_active FROM users WHERE id=%s",
                        (session['user_id'],), one=True)
        if user_active and not user_active.get('is_active'):
            session.clear()
            return jsonify({'error': 'Account disabled', 'redirect': '/'}), 401
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_admin():
            return jsonify({'error': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated

def super_admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_super_admin():
            return jsonify({'error': 'Super Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated

# ─────────────────────────────────────────────────────────
# STATIC
# ─────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/uploads/<path:filename>')
@login_required
def serve_upload(filename):
    """Serve uploaded files (photos, documents)."""
    return send_from_directory(UPLOAD_FOLDER, filename)

# ─────────────────────────────────────────────────────────
# AUTH ROUTES
# ─────────────────────────────────────────────────────────

@app.route('/api/login', methods=['POST'])
def login():
    d = request.json or {}
    user = q(
        "SELECT * FROM users WHERE username=%s AND password=%s AND is_active=TRUE",
        (d.get('username', ''), hash_pw(d.get('password', ''))),
        one=True
    )
    if not user:
        return jsonify({'error': 'Invalid username or password, or account disabled'}), 401

    token = str(uuid.uuid4())
    q("UPDATE users SET session_token=%s WHERE id=%s", (token, user['id']), commit=True)

    session.permanent = True
    session['user_id'] = user['id']
    session['username'] = user['username']
    session['role'] = user['role']
    session['session_token'] = token

    perms = get_user_perms(user['id'])
    univs = get_user_universities(user['id'])

    return jsonify({'success': True, 'user': {
        'id': user['id'], 'username': user['username'],
        'full_name': user['full_name'], 'role': user['role'],
        'permissions': perms,
        'assigned_universities': univs,
    }})

@app.route('/api/logout', methods=['POST'])
def logout():
    if 'user_id' in session:
        q("UPDATE users SET session_token=NULL WHERE id=%s",
          (session['user_id'],), commit=True)
    session.clear()
    return jsonify({'success': True})

@app.route('/api/me')
@login_required
def me():
    user = q("SELECT id, username, full_name, role FROM users WHERE id=%s",
             (session['user_id'],), one=True)
    perms = get_user_perms(session['user_id'])
    univs = get_user_universities(session['user_id'])
    return jsonify({**user, 'permissions': perms, 'assigned_universities': univs})

# ─────────────────────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────────────────────

@app.route('/api/dashboard')
@login_required
def dashboard():
    user_id = session['user_id']
    filter_sql, filter_params = student_filter_clause(user_id)

    stats = q(f"""
        SELECT
            COUNT(*)                           AS student_count,
            COALESCE(SUM(paid), 0)             AS total_collected,
            COALESCE(SUM(total_fee - paid), 0) AS outstanding,
            COALESCE(SUM(univ_fee), 0)         AS total_univ_fee
        FROM students WHERE TRUE {filter_sql}
    """, filter_params, one=True)

    # Associates & references — only if user has permission
    perms = get_user_perms(user_id)
    assoc_total = 0
    ref_total = 0
    if perms.get('can_view_associates') or is_super_admin():
        if is_super_admin():
            a = q("SELECT COALESCE(SUM(amount),0) AS t FROM associates", one=True)
        else:
            a = q("SELECT COALESCE(SUM(amount),0) AS t FROM associates WHERE created_by=%s",
                  (user_id,), one=True)
        assoc_total = float(a['t'])

    if perms.get('can_view_references') or is_super_admin():
        if is_super_admin():
            r = q("SELECT COALESCE(SUM(amount),0) AS t FROM references_", one=True)
        else:
            r = q("SELECT COALESCE(SUM(amount),0) AS t FROM references_ WHERE created_by=%s",
                  (user_id,), one=True)
        ref_total = float(r['t'])

    stats['assoc_ref_paid'] = assoc_total + ref_total

    recent = q(f"SELECT * FROM students WHERE TRUE {filter_sql} ORDER BY id DESC LIMIT 6",
               filter_params)

    fee_tracker = q(f"SELECT * FROM students WHERE TRUE {filter_sql} ORDER BY paid DESC LIMIT 5",
                    filter_params)

    # University stats based on user's accessible students
    if is_super_admin():
        univs = q("""
            SELECT u.name, u.color, COUNT(s.id) AS count
            FROM universities u
            LEFT JOIN students s ON s.university = u.name
            GROUP BY u.name, u.color ORDER BY count DESC LIMIT 10
        """)
    else:
        univs = q(f"""
            SELECT u.name, u.color, COUNT(s.id) AS count
            FROM universities u
            LEFT JOIN students s ON s.university = u.name AND s.created_by = %s
            GROUP BY u.name, u.color ORDER BY count DESC LIMIT 10
        """, (user_id,))

    # Per-user breakdown for Super Admin
    user_summary = []
    if is_super_admin():
        user_summary = q("""
            SELECT u.id, u.full_name, u.role,
                   COUNT(s.id) AS student_count,
                   COALESCE(SUM(s.paid), 0) AS total_collected,
                   COALESCE(SUM(s.total_fee - s.paid), 0) AS outstanding
            FROM users u
            LEFT JOIN students s ON s.created_by = u.id
            GROUP BY u.id, u.full_name, u.role
            ORDER BY student_count DESC
        """)
        user_summary = [{**serialize(r), 'total_collected': float(r['total_collected']),
                         'outstanding': float(r['outstanding'])} for r in user_summary]

    return jsonify({
        'stats': {k: float(v) if isinstance(v, (int, float)) else v for k, v in stats.items()},
        'recent': [serialize(r) for r in recent],
        'fee_tracker': [serialize(r) for r in fee_tracker],
        'universities': univs,
        'user_summary': user_summary,
        'permissions': perms,
    })

# ─────────────────────────────────────────────────────────
# STUDENTS
# ─────────────────────────────────────────────────────────

@app.route('/api/students', methods=['GET'])
@login_required
def get_students():
    user_id = session['user_id']
    search  = request.args.get('q', '').strip()
    univ    = request.args.get('university', '').strip()
    status  = request.args.get('status', '').strip()
    target_user = request.args.get('user_id')  # Super Admin filter by user

    filter_sql, filter_params = student_filter_clause(user_id)

    sql    = f"SELECT s.*, u.full_name AS created_by_name FROM students s LEFT JOIN users u ON u.id = s.created_by WHERE TRUE {filter_sql}"
    params = list(filter_params)

    # Super Admin can filter by specific user
    if is_super_admin() and target_user:
        sql += " AND s.created_by = %s"
        params.append(int(target_user))

    if search:
        sql += """ AND (
            s.name ILIKE %s OR s.father ILIKE %s OR
            s.mobile ILIKE %s OR s.course ILIKE %s OR
            s.university ILIKE %s OR s.enroll_no ILIKE %s
        )"""
        p = f'%{search}%'
        params += [p, p, p, p, p, p]
    if univ:
        sql += " AND s.university = %s"; params.append(univ)
    if status:
        sql += " AND s.status = %s";    params.append(status)
    sql += " ORDER BY s.id DESC"

    rows = q(sql, params)
    return jsonify([serialize(r) for r in rows])

@app.route('/api/students', methods=['POST'])
@login_required
@require_perm('can_add_student')
def add_student():
    d = request.json or {}
    user_id = session['user_id']

    # Check university access
    univs = get_user_universities(user_id)
    if univs and d.get('university') not in univs:
        return jsonify({'error': 'You are not assigned to this university'}), 403

    row = q_returning("""
        INSERT INTO students
            (created_by, name, father, mother, dob, gender, mobile, email, aadhar, address,
             course, university, batch, enroll_no, roll_no, adm_date, remarks,
             total_fee, paid, univ_fee, pay_mode, utr, doc_notes, status)
        VALUES
            (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s,%s)
        RETURNING *
    """, (
        user_id,
        d.get('name'), d.get('father'), d.get('mother'),
        d.get('dob') or None, d.get('gender'), d.get('mobile'),
        d.get('email'), d.get('aadhar'), d.get('address'),
        d.get('course'), d.get('university'), d.get('batch'),
        d.get('enroll_no'), d.get('roll_no'),
        d.get('adm_date') or None, d.get('remarks'),
        d.get('total_fee', 0), d.get('paid', 0), d.get('univ_fee', 0),
        d.get('pay_mode'), d.get('utr'), d.get('doc_notes'), 'Active'
    ))

    paid = float(d.get('paid', 0) or 0)
    if paid > 0 and row:
        conn = get_db()
        cur  = conn.cursor()
        cur.execute("""
            INSERT INTO fee_payments (student_id, recorded_by, amount, fee_type, pay_mode, ref_no, pay_date)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (row['id'], user_id, paid, 'Initial Payment',
              d.get('pay_mode'), d.get('utr'),
              d.get('adm_date') or None))
        conn.commit()

    return jsonify(serialize(row)), 201

@app.route('/api/students/<int:sid>', methods=['GET'])
@login_required
def get_student(sid):
    user_id = session['user_id']
    filter_sql, filter_params = student_filter_clause(user_id, alias='s')
    row = q(f"SELECT s.* FROM students s WHERE s.id=%s {filter_sql}",
            [sid] + list(filter_params), one=True)
    if not row:
        return jsonify({'error': 'Student not found or access denied'}), 404

    # Attach photo
    photo = q("SELECT * FROM student_photos WHERE student_id=%s ORDER BY id DESC LIMIT 1",
              (sid,), one=True)
    row['photo'] = serialize(photo) if photo else None
    return jsonify(serialize(row))

@app.route('/api/students/<int:sid>', methods=['PUT'])
@login_required
@require_perm('can_edit_student')
def update_student(sid):
    user_id = session['user_id']
    filter_sql, filter_params = student_filter_clause(user_id, alias='')

    # Verify access
    check = q(f"SELECT id FROM students WHERE id=%s {filter_sql}",
              [sid] + list(filter_params), one=True)
    if not check:
        return jsonify({'error': 'Student not found or access denied'}), 404

    d = request.json or {}
    row = q_returning("""
        UPDATE students SET
            name=%s, father=%s, mother=%s, dob=%s, gender=%s, mobile=%s,
            email=%s, aadhar=%s, address=%s, course=%s, university=%s, batch=%s,
            enroll_no=%s, roll_no=%s, adm_date=%s, remarks=%s,
            total_fee=%s, univ_fee=%s, pay_mode=%s, utr=%s,
            doc_notes=%s, status=%s
        WHERE id=%s
        RETURNING *
    """, (
        d.get('name'), d.get('father'), d.get('mother'),
        d.get('dob') or None, d.get('gender'), d.get('mobile'),
        d.get('email'), d.get('aadhar'), d.get('address'),
        d.get('course'), d.get('university'), d.get('batch'),
        d.get('enroll_no'), d.get('roll_no'),
        d.get('adm_date') or None, d.get('remarks'),
        d.get('total_fee', 0), d.get('univ_fee', 0),
        d.get('pay_mode'), d.get('utr'),
        d.get('doc_notes'), d.get('status', 'Active'),
        sid
    ))
    return jsonify(serialize(row))

@app.route('/api/students/<int:sid>', methods=['DELETE'])
@login_required
@require_perm('can_delete_student')
def delete_student(sid):
    user_id = session['user_id']
    filter_sql, filter_params = student_filter_clause(user_id)
    check = q(f"SELECT id FROM students WHERE id=%s {filter_sql}",
              [sid] + list(filter_params), one=True)
    if not check:
        return jsonify({'error': 'Student not found or access denied'}), 404
    q("DELETE FROM students WHERE id=%s", (sid,), commit=True)
    return jsonify({'success': True})

# ─────────────────────────────────────────────────────────
# PHOTO UPLOAD
# ─────────────────────────────────────────────────────────

@app.route('/api/students/<int:sid>/photo', methods=['POST'])
@login_required
def upload_student_photo(sid):
    user_id = session['user_id']
    filter_sql, filter_params = student_filter_clause(user_id)
    check = q(f"SELECT id FROM students WHERE id=%s {filter_sql}",
              [sid] + list(filter_params), one=True)
    if not check:
        return jsonify({'error': 'Student not found or access denied'}), 404

    if 'photo' not in request.files:
        return jsonify({'error': 'No photo file provided'}), 400

    file = request.files['photo']
    if file.filename == '' or not allowed_file(file.filename):
        return jsonify({'error': 'Invalid file type'}), 400

    ext = file.filename.rsplit('.', 1)[1].lower()
    filename = f"student_{sid}_photo_{uuid.uuid4().hex[:8]}.{ext}"
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)

    # Update student record
    q("UPDATE students SET photo_path=%s WHERE id=%s", (filename, sid), commit=True)

    # Insert into student_photos
    q_returning("""
        INSERT INTO student_photos (student_id, file_path, file_name, uploaded_by)
        VALUES (%s, %s, %s, %s) RETURNING id
    """, (sid, filename, file.filename, user_id))

    return jsonify({'success': True, 'filename': filename, 'url': f'/uploads/{filename}'})

@app.route('/api/students/<int:sid>/photo', methods=['GET'])
@login_required
def get_student_photo(sid):
    photo = q("SELECT * FROM student_photos WHERE student_id=%s ORDER BY id DESC LIMIT 1",
              (sid,), one=True)
    if not photo:
        return jsonify({'photo': None})
    return jsonify({'photo': serialize(photo), 'url': f'/uploads/{photo["file_path"]}'})

# ─────────────────────────────────────────────────────────
# FEE PAYMENTS
# ─────────────────────────────────────────────────────────

@app.route('/api/students/<int:sid>/payments', methods=['GET'])
@login_required
@require_perm('can_view_payments')
def get_payments(sid):
    user_id = session['user_id']
    filter_sql, filter_params = student_filter_clause(user_id)
    check = q(f"SELECT id FROM students WHERE id=%s {filter_sql}",
              [sid] + list(filter_params), one=True)
    if not check:
        return jsonify({'error': 'Access denied'}), 403
    rows = q("""
        SELECT fp.*, u.full_name AS recorded_by_name
        FROM fee_payments fp
        LEFT JOIN users u ON u.id = fp.recorded_by
        WHERE fp.student_id=%s ORDER BY fp.id DESC
    """, (sid,))
    return jsonify([serialize(r) for r in rows])

@app.route('/api/students/<int:sid>/payments', methods=['POST'])
@login_required
@require_perm('can_add_payment')
def add_payment(sid):
    user_id = session['user_id']
    d      = request.json or {}
    amount = float(d.get('amount', 0) or 0)

    filter_sql, filter_params = student_filter_clause(user_id)
    student = q(f"SELECT * FROM students WHERE id=%s {filter_sql}",
                [sid] + list(filter_params), one=True)
    if not student:
        return jsonify({'error': 'Student not found or access denied'}), 404

    new_paid = min(float(student['paid']) + amount, float(student['total_fee']))
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("UPDATE students SET paid=%s WHERE id=%s", (new_paid, sid))
    cur.execute("""
        INSERT INTO fee_payments
            (student_id, recorded_by, amount, fee_type, pay_mode, ref_no, pay_date, remarks)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
    """, (sid, user_id, amount,
          d.get('fee_type', 'Tuition Fee'),
          d.get('pay_mode', 'Cash'),
          d.get('ref_no', ''),
          d.get('pay_date') or datetime.now().date().isoformat(),
          d.get('remarks', '')))
    conn.commit()

    updated = q("SELECT * FROM students WHERE id=%s", (sid,), one=True)
    return jsonify({'success': True, 'new_paid': new_paid, 'student': serialize(updated)})

# Payment summary per user (for Super Admin)
@app.route('/api/payments/summary')
@login_required
def payments_summary():
    user_id = session['user_id']
    if is_super_admin():
        # Get per-user payment summary
        rows = q("""
            SELECT u.id, u.full_name,
                   COUNT(fp.id) AS payment_count,
                   COALESCE(SUM(fp.amount), 0) AS total_amount
            FROM users u
            LEFT JOIN fee_payments fp ON fp.recorded_by = u.id
            GROUP BY u.id, u.full_name
            ORDER BY total_amount DESC
        """)
        return jsonify([{**serialize(r), 'total_amount': float(r['total_amount'])} for r in rows])
    else:
        row = q("""
            SELECT COUNT(fp.id) AS payment_count,
                   COALESCE(SUM(fp.amount), 0) AS total_amount
            FROM fee_payments fp
            WHERE fp.recorded_by = %s
        """, (user_id,), one=True)
        return jsonify(serialize(row))

# ─────────────────────────────────────────────────────────
# DOCUMENTS (with file upload)
# ─────────────────────────────────────────────────────────

@app.route('/api/documents', methods=['GET'])
@login_required
@require_perm('can_view_documents')
def get_documents():
    user_id = session['user_id']
    sid = request.args.get('student_id')
    filter_sql, filter_params = student_filter_clause(user_id, alias='s')

    sql = """
        SELECT d.*, s.name AS student_name, u.full_name AS uploaded_by_name
        FROM documents d
        LEFT JOIN students s ON s.id = d.student_id
        LEFT JOIN users u ON u.id = d.uploaded_by
        WHERE TRUE
    """
    params = []

    if not is_super_admin():
        # Only show docs for students this user can see
        sql += f" AND (d.student_id IS NULL OR d.student_id IN (SELECT id FROM students WHERE TRUE {filter_sql}))"
        params.extend(filter_params)

    if sid:
        sql += " AND d.student_id = %s"; params.append(int(sid))

    sql += " ORDER BY d.id DESC"
    rows = q(sql, params)
    result = []
    for r in rows:
        sr = serialize(r)
        if r.get('file_path'):
            sr['file_url'] = f'/uploads/{r["file_path"]}'
        result.append(sr)
    return jsonify(result)

@app.route('/api/documents', methods=['POST'])
@login_required
@require_perm('can_upload_document')
def add_document():
    user_id = session['user_id']
    d = request.json or {}
    q_returning("""
        INSERT INTO documents (student_id, student, doc_type, university, issue_date, status, delivered_to, uploaded_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
    """, (d.get('student_id'), d.get('student'), d.get('doc_type'), d.get('university'),
          d.get('issue_date') or None, d.get('status', 'Delivered'), d.get('delivered_to'), user_id))
    return jsonify({'success': True}), 201

@app.route('/api/documents/<int:did>/upload', methods=['POST'])
@login_required
@require_perm('can_upload_document')
def upload_document_file(did):
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    if file.filename == '' or not allowed_file(file.filename):
        return jsonify({'error': 'Invalid file type'}), 400

    ext = file.filename.rsplit('.', 1)[1].lower()
    filename = f"doc_{did}_{uuid.uuid4().hex[:8]}.{ext}"
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)

    q("UPDATE documents SET file_path=%s, file_name=%s, uploaded_by=%s WHERE id=%s",
      (filename, file.filename, session['user_id'], did), commit=True)

    return jsonify({'success': True, 'filename': filename, 'url': f'/uploads/{filename}'})

@app.route('/api/documents/<int:did>', methods=['DELETE'])
@login_required
@admin_required
def delete_document(did):
    doc = q("SELECT file_path FROM documents WHERE id=%s", (did,), one=True)
    if doc and doc.get('file_path'):
        try:
            os.remove(os.path.join(UPLOAD_FOLDER, doc['file_path']))
        except Exception:
            pass
    q("DELETE FROM documents WHERE id=%s", (did,), commit=True)
    return jsonify({'success': True})

# ─────────────────────────────────────────────────────────
# ASSOCIATES
# ─────────────────────────────────────────────────────────

@app.route('/api/associates', methods=['GET'])
@login_required
@require_perm('can_view_associates')
def get_associates():
    user_id = session['user_id']
    if is_super_admin():
        rows = q("SELECT a.*, u.full_name AS created_by_name FROM associates a LEFT JOIN users u ON u.id=a.created_by ORDER BY a.id DESC")
    else:
        rows = q("SELECT a.*, u.full_name AS created_by_name FROM associates a LEFT JOIN users u ON u.id=a.created_by WHERE a.created_by=%s ORDER BY a.id DESC",
                 (user_id,))
    return jsonify([serialize(r) for r in rows])

@app.route('/api/associates', methods=['POST'])
@login_required
@require_perm('can_manage_associates')
def add_associate():
    d   = request.json or {}
    row = q_returning("""
        INSERT INTO associates (created_by,name,phone,student,work_done,amount,pay_date,pay_mode,utr,status,notes)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *
    """, (session['user_id'], d.get('name'), d.get('phone'), d.get('student'), d.get('work_done'),
          d.get('amount', 0), d.get('pay_date') or None, d.get('pay_mode', 'Cash'),
          d.get('utr'), d.get('status', 'Paid'), d.get('notes')))
    return jsonify(serialize(row)), 201

@app.route('/api/associates/<int:aid>', methods=['DELETE'])
@login_required
@require_perm('can_manage_associates')
def delete_associate(aid):
    if not is_super_admin():
        check = q("SELECT id FROM associates WHERE id=%s AND created_by=%s",
                  (aid, session['user_id']), one=True)
        if not check:
            return jsonify({'error': 'Access denied'}), 403
    q("DELETE FROM associates WHERE id=%s", (aid,), commit=True)
    return jsonify({'success': True})

# ─────────────────────────────────────────────────────────
# REFERENCES
# ─────────────────────────────────────────────────────────

@app.route('/api/references', methods=['GET'])
@login_required
@require_perm('can_view_references')
def get_references():
    user_id = session['user_id']
    if is_super_admin():
        rows = q("SELECT r.*, u.full_name AS created_by_name FROM references_ r LEFT JOIN users u ON u.id=r.created_by ORDER BY r.id DESC")
    else:
        rows = q("SELECT r.*, u.full_name AS created_by_name FROM references_ r LEFT JOIN users u ON u.id=r.created_by WHERE r.created_by=%s ORDER BY r.id DESC",
                 (user_id,))
    return jsonify([serialize(r) for r in rows])

@app.route('/api/references', methods=['POST'])
@login_required
@require_perm('can_manage_references')
def add_reference():
    d   = request.json or {}
    row = q_returning("""
        INSERT INTO references_ (created_by,name,phone,student,university,amount,pay_date,pay_mode,utr,status,notes)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *
    """, (session['user_id'], d.get('name'), d.get('phone'), d.get('student'), d.get('university'),
          d.get('amount', 0), d.get('pay_date') or None, d.get('pay_mode', 'Cash'),
          d.get('utr'), d.get('status', 'Paid'), d.get('notes')))
    return jsonify(serialize(row)), 201

@app.route('/api/references/<int:rid>', methods=['DELETE'])
@login_required
@require_perm('can_manage_references')
def delete_reference(rid):
    if not is_super_admin():
        check = q("SELECT id FROM references_ WHERE id=%s AND created_by=%s",
                  (rid, session['user_id']), one=True)
        if not check:
            return jsonify({'error': 'Access denied'}), 403
    q("DELETE FROM references_ WHERE id=%s", (rid,), commit=True)
    return jsonify({'success': True})

# ─────────────────────────────────────────────────────────
# UNIVERSITIES
# ─────────────────────────────────────────────────────────

@app.route('/api/universities', methods=['GET'])
@login_required
def get_universities():
    user_id = session['user_id']
    assigned = get_user_universities(user_id)

    rows = q("""
        SELECT u.id, u.name, u.state, u.color,
               COUNT(s.id)::int AS student_count
        FROM universities u
        LEFT JOIN students s ON s.university = u.name
        GROUP BY u.id, u.name, u.state, u.color
        ORDER BY u.name
    """)

    if assigned:  # Non-super-admin with assigned universities
        rows = [r for r in rows if r['name'] in assigned]

    return jsonify(rows)

@app.route('/api/universities', methods=['POST'])
@login_required
@require_perm('can_manage_universities')
def add_university():
    d = request.json or {}
    try:
        q_returning("""
            INSERT INTO universities (name, state, color)
            VALUES (%s, %s, %s) RETURNING id
        """, (d.get('name'), d.get('state'), d.get('color', '#1A6CF6')))
    except psycopg2.errors.UniqueViolation:
        get_db().rollback()
        return jsonify({'error': 'University already exists'}), 409
    return jsonify({'success': True}), 201

@app.route('/api/universities/<int:uid>', methods=['PUT'])
@login_required
@require_perm('can_manage_universities')
def update_university(uid):
    d = request.json or {}
    q("UPDATE universities SET name=%s, state=%s, color=%s WHERE id=%s",
      (d.get('name'), d.get('state'), d.get('color', '#1A6CF6'), uid), commit=True)
    return jsonify({'success': True})

@app.route('/api/universities/<int:uid>', methods=['DELETE'])
@login_required
@require_perm('can_manage_universities')
def delete_university(uid):
    q("DELETE FROM universities WHERE id=%s", (uid,), commit=True)
    return jsonify({'success': True})

# ─────────────────────────────────────────────────────────
# USERS (Super Admin manages all, Admin manages Staff)
# ─────────────────────────────────────────────────────────

@app.route('/api/users', methods=['GET'])
@login_required
@admin_required
def get_users():
    rows = q("SELECT id, username, full_name, role, is_active, created_at FROM users ORDER BY id")
    result = []
    for r in rows:
        sr = serialize(r)
        perms = get_user_perms(r['id'])
        univs = q("""
            SELECT u.id, u.name FROM user_universities uu
            JOIN universities u ON u.id = uu.university_id
            WHERE uu.user_id = %s
        """, (r['id'],))
        sr['permissions'] = perms
        sr['assigned_universities'] = univs
        result.append(sr)
    return jsonify(result)

@app.route('/api/users', methods=['POST'])
@login_required
@admin_required
def add_user():
    d = request.json or {}
    try:
        new_user = q_returning("""
            INSERT INTO users (username, password, full_name, role)
            VALUES (%s,%s,%s,%s) RETURNING id
        """, (d.get('username'), hash_pw(d.get('password', '')),
              d.get('full_name'), d.get('role', 'Staff')))
    except psycopg2.errors.UniqueViolation:
        get_db().rollback()
        return jsonify({'error': 'Username already exists'}), 409

    uid = new_user['id']

    # Set permissions
    perms = d.get('permissions', {})
    q_returning("""
        INSERT INTO user_permissions (user_id,
            can_add_student, can_edit_student, can_delete_student,
            can_view_payments, can_add_payment,
            can_view_associates, can_manage_associates,
            can_view_references, can_manage_references,
            can_view_documents, can_upload_document,
            can_view_reports, can_manage_universities, can_view_all_students)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id
    """, (uid,
          perms.get('can_add_student', True),
          perms.get('can_edit_student', True),
          perms.get('can_delete_student', False),
          perms.get('can_view_payments', True),
          perms.get('can_add_payment', True),
          perms.get('can_view_associates', False),
          perms.get('can_manage_associates', False),
          perms.get('can_view_references', False),
          perms.get('can_manage_references', False),
          perms.get('can_view_documents', True),
          perms.get('can_upload_document', True),
          perms.get('can_view_reports', False),
          perms.get('can_manage_universities', False),
          perms.get('can_view_all_students', False),
    ))

    # Assign universities
    assigned_univ_ids = d.get('assigned_university_ids', [])
    for univ_id in assigned_univ_ids:
        try:
            q_returning("""
                INSERT INTO user_universities (user_id, university_id) VALUES (%s,%s) RETURNING id
            """, (uid, univ_id))
        except Exception:
            get_db().rollback()

    return jsonify({'success': True, 'id': uid}), 201

@app.route('/api/users/<int:uid>', methods=['PUT'])
@login_required
@admin_required
def update_user(uid):
    d = request.json or {}

    # Update basic info
    if d.get('full_name') or d.get('role'):
        q("UPDATE users SET full_name=%s, role=%s WHERE id=%s",
          (d.get('full_name'), d.get('role'), uid), commit=True)

    # Reset password if provided
    if d.get('new_password'):
        if len(d['new_password']) < 6:
            return jsonify({'error': 'Password must be at least 6 characters'}), 400
        q("UPDATE users SET password=%s WHERE id=%s",
          (hash_pw(d['new_password']), uid), commit=True)

    # Update permissions
    if 'permissions' in d:
        perms = d['permissions']
        existing = q("SELECT id FROM user_permissions WHERE user_id=%s", (uid,), one=True)
        if existing:
            q("""UPDATE user_permissions SET
                can_add_student=%s, can_edit_student=%s, can_delete_student=%s,
                can_view_payments=%s, can_add_payment=%s,
                can_view_associates=%s, can_manage_associates=%s,
                can_view_references=%s, can_manage_references=%s,
                can_view_documents=%s, can_upload_document=%s,
                can_view_reports=%s, can_manage_universities=%s, can_view_all_students=%s
                WHERE user_id=%s""",
               (perms.get('can_add_student', True),
                perms.get('can_edit_student', True),
                perms.get('can_delete_student', False),
                perms.get('can_view_payments', True),
                perms.get('can_add_payment', True),
                perms.get('can_view_associates', False),
                perms.get('can_manage_associates', False),
                perms.get('can_view_references', False),
                perms.get('can_manage_references', False),
                perms.get('can_view_documents', True),
                perms.get('can_upload_document', True),
                perms.get('can_view_reports', False),
                perms.get('can_manage_universities', False),
                perms.get('can_view_all_students', False),
                uid), commit=True)
        else:
            q_returning("""
                INSERT INTO user_permissions (user_id,
                    can_add_student, can_edit_student, can_delete_student,
                    can_view_payments, can_add_payment,
                    can_view_associates, can_manage_associates,
                    can_view_references, can_manage_references,
                    can_view_documents, can_upload_document,
                    can_view_reports, can_manage_universities, can_view_all_students)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
            """, (uid,
                  perms.get('can_add_student', True),
                  perms.get('can_edit_student', True),
                  perms.get('can_delete_student', False),
                  perms.get('can_view_payments', True),
                  perms.get('can_add_payment', True),
                  perms.get('can_view_associates', False),
                  perms.get('can_manage_associates', False),
                  perms.get('can_view_references', False),
                  perms.get('can_manage_references', False),
                  perms.get('can_view_documents', True),
                  perms.get('can_upload_document', True),
                  perms.get('can_view_reports', False),
                  perms.get('can_manage_universities', False),
                  perms.get('can_view_all_students', False),
            ))

    # Update university assignments
    if 'assigned_university_ids' in d:
        q("DELETE FROM user_universities WHERE user_id=%s", (uid,), commit=True)
        for univ_id in d['assigned_university_ids']:
            try:
                q_returning("""
                    INSERT INTO user_universities (user_id, university_id) VALUES (%s,%s) RETURNING id
                """, (uid, univ_id))
            except Exception:
                get_db().rollback()

    return jsonify({'success': True})

@app.route('/api/users/<int:uid>/toggle-active', methods=['POST'])
@login_required
@admin_required
def toggle_user_active(uid):
    if uid == session.get('user_id'):
        return jsonify({'error': 'Cannot disable yourself'}), 400
    user = q("SELECT is_active FROM users WHERE id=%s", (uid,), one=True)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    new_state = not user['is_active']
    q("UPDATE users SET is_active=%s WHERE id=%s", (new_state, uid), commit=True)
    # Force logout if disabling
    if not new_state:
        q("UPDATE users SET session_token=NULL WHERE id=%s", (uid,), commit=True)
    return jsonify({'success': True, 'is_active': new_state})

@app.route('/api/users/<int:uid>/force-logout', methods=['POST'])
@login_required
@admin_required
def force_logout_user(uid):
    """Force a user's session to expire immediately."""
    if uid == session.get('user_id'):
        return jsonify({'error': 'Cannot force-logout yourself'}), 400
    q("UPDATE users SET session_token=NULL WHERE id=%s", (uid,), commit=True)
    return jsonify({'success': True, 'message': 'User session revoked'})

@app.route('/api/users/<int:uid>', methods=['DELETE'])
@login_required
@admin_required
def delete_user(uid):
    if uid == session.get('user_id'):
        return jsonify({'error': 'Cannot delete yourself'}), 400
    q("DELETE FROM users WHERE id=%s", (uid,), commit=True)
    return jsonify({'success': True})

@app.route('/api/change-password', methods=['POST'])
@login_required
def change_password():
    d    = request.json or {}
    user = q("SELECT * FROM users WHERE id=%s", (session['user_id'],), one=True)
    if user['password'] != hash_pw(d.get('old_password', '')):
        return jsonify({'error': 'Current password is wrong'}), 400
    new_pw = d.get('new_password', '')
    if len(new_pw) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400
    q("UPDATE users SET password=%s WHERE id=%s",
      (hash_pw(new_pw), session['user_id']), commit=True)
    return jsonify({'success': True})

# ─────────────────────────────────────────────────────────
# REPORTS (user-aware)
# ─────────────────────────────────────────────────────────

def csv_response(rows, headers, filename):
    buf = io.StringIO()
    w   = csv.writer(buf)
    w.writerow(headers)
    w.writerows(rows)
    return Response(
        '\ufeff' + buf.getvalue(),
        mimetype='text/csv; charset=utf-8-sig',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )

@app.route('/api/reports/students')
@login_required
@require_perm('can_view_reports')
def report_students():
    user_id = session['user_id']
    filter_sql, filter_params = student_filter_clause(user_id)
    data = q(f"SELECT * FROM students WHERE TRUE {filter_sql} ORDER BY name", filter_params)
    rows = [[s['id'],s['name'],s['father'],s['mobile'],s['course'],
             s['university'],s['batch'],s['enroll_no'],
             s['adm_date'],float(s['total_fee']),float(s['paid']),
             float(s['total_fee'])-float(s['paid']),s['status']]
            for s in data]
    return csv_response(rows,
        ['ID','Name','Father','Mobile','Course','University','Batch',
         'Enrollment No','Admission Date','Total Fee','Paid','Balance','Status'],
        f'Students_{datetime.now().strftime("%Y%m%d")}.csv')

@app.route('/api/reports/fees')
@login_required
@require_perm('can_view_reports')
def report_fees():
    user_id = session['user_id']
    filter_sql, filter_params = student_filter_clause(user_id)
    data = q(f"SELECT * FROM students WHERE TRUE {filter_sql} ORDER BY name", filter_params)
    rows = [[s['name'],s['university'],s['course'],
             float(s['total_fee']),float(s['paid']),
             float(s['total_fee'])-float(s['paid']),
             'Cleared' if float(s['total_fee'])==float(s['paid']) else 'Pending']
            for s in data]
    return csv_response(rows,
        ['Student','University','Course','Total Fee','Paid','Balance','Status'],
        f'Fees_{datetime.now().strftime("%Y%m%d")}.csv')

@app.route('/api/reports/outstanding')
@login_required
@require_perm('can_view_reports')
def report_outstanding():
    user_id = session['user_id']
    filter_sql, filter_params = student_filter_clause(user_id)
    data = q(f"SELECT * FROM students WHERE paid < total_fee {filter_sql} ORDER BY (total_fee-paid) DESC",
             filter_params)
    rows = [[s['name'],s['mobile'],s['university'],s['course'],
             float(s['total_fee']),float(s['paid']),
             float(s['total_fee'])-float(s['paid'])]
            for s in data]
    return csv_response(rows,
        ['Student','Mobile','University','Course','Total Fee','Paid','Outstanding'],
        f'Outstanding_{datetime.now().strftime("%Y%m%d")}.csv')

@app.route('/api/reports/assoc-ref')
@login_required
@require_perm('can_view_reports')
def report_assoc_ref():
    user_id = session['user_id']
    if is_super_admin():
        assocs = q("SELECT * FROM associates ORDER BY pay_date DESC")
        refs   = q("SELECT * FROM references_ ORDER BY pay_date DESC")
    else:
        assocs = q("SELECT * FROM associates WHERE created_by=%s ORDER BY pay_date DESC", (user_id,))
        refs   = q("SELECT * FROM references_ WHERE created_by=%s ORDER BY pay_date DESC", (user_id,))
    rows = []
    for a in assocs:
        rows.append(['Associate', a['name'], a['phone'], a['student'],
                     a['work_done'], float(a['amount']), a['pay_date'],
                     a['pay_mode'], a['utr'], a['notes']])
    for r in refs:
        rows.append(['Reference', r['name'], r['phone'], r['student'],
                     'Referral Incentive', float(r['amount']), r['pay_date'],
                     r['pay_mode'], r['utr'], r['notes']])
    return csv_response(rows,
        ['Type','Name','Phone','Student','Work/Reason','Amount',
         'Date','Mode','UTR','Notes'],
        f'AssocRef_{datetime.now().strftime("%Y%m%d")}.csv')

# Super Admin: per-user report
@app.route('/api/reports/user/<int:uid>/students')
@login_required
@super_admin_required
def report_user_students(uid):
    data = q("SELECT * FROM students WHERE created_by=%s ORDER BY name", (uid,))
    user = q("SELECT full_name FROM users WHERE id=%s", (uid,), one=True)
    rows = [[s['id'],s['name'],s['mobile'],s['course'],s['university'],
             float(s['total_fee']),float(s['paid']),
             float(s['total_fee'])-float(s['paid']),s['status']]
            for s in data]
    uname = user['full_name'].replace(' ', '_') if user else str(uid)
    return csv_response(rows,
        ['ID','Name','Mobile','Course','University','Total Fee','Paid','Balance','Status'],
        f'{uname}_Students_{datetime.now().strftime("%Y%m%d")}.csv')

# ─────────────────────────────────────────────────────────
# BALANCE OVERVIEW
# ─────────────────────────────────────────────────────────

@app.route('/api/balance/overview')
@login_required
def balance_overview():
    """User sees their own balance. Super Admin sees all + per-user breakdown."""
    user_id = session['user_id']
    filter_sql, filter_params = student_filter_clause(user_id)

    my_stats = q(f"""
        SELECT
            COALESCE(SUM(total_fee), 0) AS total_fee,
            COALESCE(SUM(paid), 0) AS total_paid,
            COALESCE(SUM(total_fee - paid), 0) AS outstanding,
            COALESCE(SUM(univ_fee), 0) AS univ_fee_total,
            COUNT(*) AS student_count
        FROM students WHERE TRUE {filter_sql}
    """, filter_params, one=True)

    result = {k: float(v) if hasattr(v, '__float__') else v for k, v in my_stats.items()}

    if is_super_admin():
        per_user = q("""
            SELECT u.id, u.full_name,
                   COALESCE(SUM(s.total_fee), 0) AS total_fee,
                   COALESCE(SUM(s.paid), 0) AS total_paid,
                   COALESCE(SUM(s.total_fee - s.paid), 0) AS outstanding,
                   COUNT(s.id) AS student_count
            FROM users u
            LEFT JOIN students s ON s.created_by = u.id
            WHERE u.role != 'Super Admin'
            GROUP BY u.id, u.full_name
            ORDER BY total_paid DESC
        """)
        result['per_user'] = [{
            'id': r['id'], 'full_name': r['full_name'],
            'total_fee': float(r['total_fee']), 'total_paid': float(r['total_paid']),
            'outstanding': float(r['outstanding']), 'student_count': r['student_count']
        } for r in per_user]

    return jsonify(result)

# ─────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV', 'development') == 'development'
    print(f"\n{'='*55}")
    print(f"  Sky Eduworld — Management System (UPGRADED)")
    print(f"{'='*55}")
    print(f"  URL:    http://localhost:{port}")
    print(f"  Login:  admin / sky@2024")
    print(f"  Uploads folder: {UPLOAD_FOLDER}")
    print(f"{'='*55}\n")
    app.run(host='0.0.0.0', port=port, debug=debug)
