"""
Sky Eduworld — Management System
Backend: Flask + PostgreSQL
"""

import os, csv, io, hashlib
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
# Initialize database on startup
with app.app_context():
    init_db()
DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://postgres:password@localhost:5432/sky_eduworld')

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
    """Execute a query. Returns list of dicts, one dict, or None."""
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
    """Execute INSERT/UPDATE with RETURNING, returns the dict."""
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
        id         SERIAL PRIMARY KEY,
        username   TEXT UNIQUE NOT NULL,
        password   TEXT NOT NULL,
        full_name  TEXT NOT NULL,
        role       TEXT NOT NULL DEFAULT 'Staff',
        created_at TIMESTAMP DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS universities (
        id    SERIAL PRIMARY KEY,
        name  TEXT UNIQUE NOT NULL,
        state TEXT,
        color TEXT DEFAULT '#1A6CF6'
    );

    CREATE TABLE IF NOT EXISTS students (
        id          SERIAL PRIMARY KEY,
        name        TEXT NOT NULL,
        father      TEXT,
        mother      TEXT,
        dob         DATE,
        gender      TEXT,
        mobile      TEXT,
        email       TEXT,
        aadhar      TEXT,
        address     TEXT,
        course      TEXT,
        university  TEXT,
        batch       TEXT,
        enroll_no   TEXT,
        roll_no     TEXT,
        adm_date    DATE,
        remarks     TEXT,
        total_fee   NUMERIC(12,2) DEFAULT 0,
        paid        NUMERIC(12,2) DEFAULT 0,
        univ_fee    NUMERIC(12,2) DEFAULT 0,
        pay_mode    TEXT,
        utr         TEXT,
        doc_notes   TEXT,
        status      TEXT DEFAULT 'Active',
        created_at  TIMESTAMP DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS fee_payments (
        id          SERIAL PRIMARY KEY,
        student_id  INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
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

    CREATE TABLE IF NOT EXISTS documents (
        id           SERIAL PRIMARY KEY,
        student      TEXT NOT NULL,
        doc_type     TEXT NOT NULL,
        university   TEXT,
        issue_date   DATE,
        status       TEXT DEFAULT 'Delivered',
        delivered_to TEXT,
        created_at   TIMESTAMP DEFAULT NOW()
    );
    """)

    # Default admin
    cur.execute("SELECT id FROM users WHERE username='admin'")
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO users (username, password, full_name, role) VALUES (%s, %s, %s, %s)",
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
        ('Monad University',                'Uttar Pradesh',   '#7C3AED'),
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
# AUTH
# ─────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Not authenticated', 'redirect': '/'}), 401
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') not in ('Admin', 'Super Admin'):
            return jsonify({'error': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated

# ─────────────────────────────────────────────────────────
# STATIC
# ─────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

# ─────────────────────────────────────────────────────────
# AUTH ROUTES
# ─────────────────────────────────────────────────────────

@app.route('/api/login', methods=['POST'])
def login():
    d = request.json or {}
    user = q(
        "SELECT * FROM users WHERE username=%s AND password=%s",
        (d.get('username',''), hash_pw(d.get('password',''))),
        one=True
    )
    if not user:
        return jsonify({'error': 'Invalid username or password'}), 401
    session.permanent = True
    session['user_id'] = user['id']
    session['username'] = user['username']
    session['role'] = user['role']
    return jsonify({'success': True, 'user': {
        'id': user['id'], 'username': user['username'],
        'full_name': user['full_name'], 'role': user['role']
    }})

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})

@app.route('/api/me')
@login_required
def me():
    user = q("SELECT id, username, full_name, role FROM users WHERE id=%s",
             (session['user_id'],), one=True)
    return jsonify(user)

# ─────────────────────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────────────────────

@app.route('/api/dashboard')
@login_required
def dashboard():
    stats = q("""
        SELECT
            COUNT(*)                          AS student_count,
            COALESCE(SUM(paid), 0)            AS total_collected,
            COALESCE(SUM(total_fee - paid), 0) AS outstanding
        FROM students
    """, one=True)

    assoc_total = q("SELECT COALESCE(SUM(amount),0) AS t FROM associates", one=True)['t']
    ref_total   = q("SELECT COALESCE(SUM(amount),0) AS t FROM references_", one=True)['t']
    stats['assoc_ref_paid'] = float(assoc_total) + float(ref_total)

    recent = q("SELECT * FROM students ORDER BY id DESC LIMIT 6")
    fee_tracker = q("SELECT * FROM students ORDER BY paid DESC LIMIT 5")

    univs = q("""
        SELECT u.name, u.color, COUNT(s.id) AS count
        FROM universities u
        LEFT JOIN students s ON s.university = u.name
        GROUP BY u.name, u.color
        ORDER BY count DESC
        LIMIT 8
    """)

    return jsonify({
        'stats': {k: float(v) if isinstance(v, (int, float)) else v for k, v in stats.items()},
        'recent': [serialize(r) for r in recent],
        'fee_tracker': [serialize(r) for r in fee_tracker],
        'universities': univs,
    })

# ─────────────────────────────────────────────────────────
# STUDENTS
# ─────────────────────────────────────────────────────────

def serialize(row):
    """Convert Decimal/date objects to JSON-safe types."""
    out = {}
    for k, v in row.items():
        if hasattr(v, 'isoformat'):
            out[k] = v.isoformat()
        elif hasattr(v, '__float__'):
            out[k] = float(v)
        else:
            out[k] = v
    return out

@app.route('/api/students', methods=['GET'])
@login_required
def get_students():
    search   = request.args.get('q', '').strip()
    univ     = request.args.get('university', '').strip()
    status   = request.args.get('status', '').strip()

    sql    = "SELECT * FROM students WHERE TRUE"
    params = []

    if search:
        sql += """ AND (
            name      ILIKE %s OR father   ILIKE %s OR
            mobile    ILIKE %s OR course   ILIKE %s OR
            university ILIKE %s OR enroll_no ILIKE %s
        )"""
        p = f'%{search}%'
        params += [p, p, p, p, p, p]
    if univ:
        sql += " AND university = %s";  params.append(univ)
    if status:
        sql += " AND status = %s";      params.append(status)
    sql += " ORDER BY id DESC"

    rows = q(sql, params)
    return jsonify([serialize(r) for r in rows])

@app.route('/api/students', methods=['POST'])
@login_required
def add_student():
    d = request.json or {}
    row = q_returning("""
        INSERT INTO students
            (name, father, mother, dob, gender, mobile, email, aadhar, address,
             course, university, batch, enroll_no, roll_no, adm_date, remarks,
             total_fee, paid, univ_fee, pay_mode, utr, doc_notes, status)
        VALUES
            (%s,%s,%s,%s,%s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s,%s)
        RETURNING *
    """, (
        d.get('name'), d.get('father'), d.get('mother'),
        d.get('dob') or None, d.get('gender'), d.get('mobile'),
        d.get('email'), d.get('aadhar'), d.get('address'),
        d.get('course'), d.get('university'), d.get('batch'),
        d.get('enroll_no'), d.get('roll_no'),
        d.get('adm_date') or None, d.get('remarks'),
        d.get('total_fee', 0), d.get('paid', 0), d.get('univ_fee', 0),
        d.get('pay_mode'), d.get('utr'), d.get('doc_notes'), 'Active'
    ))

    # Record initial payment
    paid = float(d.get('paid', 0) or 0)
    if paid > 0 and row:
        conn = get_db()
        cur  = conn.cursor()
        cur.execute("""
            INSERT INTO fee_payments (student_id, amount, fee_type, pay_mode, ref_no, pay_date)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (row['id'], paid, 'Initial Payment',
              d.get('pay_mode'), d.get('utr'),
              d.get('adm_date') or None))
        conn.commit()

    return jsonify(serialize(row)), 201

@app.route('/api/students/<int:sid>', methods=['PUT'])
@login_required
def update_student(sid):
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
def delete_student(sid):
    q("DELETE FROM students WHERE id=%s", (sid,), commit=True)
    return jsonify({'success': True})

# ─────────────────────────────────────────────────────────
# FEE PAYMENTS
# ─────────────────────────────────────────────────────────

@app.route('/api/students/<int:sid>/payments', methods=['GET'])
@login_required
def get_payments(sid):
    rows = q("SELECT * FROM fee_payments WHERE student_id=%s ORDER BY id DESC", (sid,))
    return jsonify([serialize(r) for r in rows])

@app.route('/api/students/<int:sid>/payments', methods=['POST'])
@login_required
def add_payment(sid):
    d      = request.json or {}
    amount = float(d.get('amount', 0) or 0)

    student = q("SELECT * FROM students WHERE id=%s", (sid,), one=True)
    if not student:
        return jsonify({'error': 'Student not found'}), 404

    new_paid = min(float(student['paid']) + amount, float(student['total_fee']))

    conn = get_db()
    cur  = conn.cursor()
    cur.execute("UPDATE students SET paid=%s WHERE id=%s", (new_paid, sid))
    cur.execute("""
        INSERT INTO fee_payments
            (student_id, amount, fee_type, pay_mode, ref_no, pay_date, remarks)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
    """, (sid, amount,
          d.get('fee_type', 'Tuition Fee'),
          d.get('pay_mode', 'Cash'),
          d.get('ref_no', ''),
          d.get('pay_date') or datetime.now().date().isoformat(),
          d.get('remarks', '')))
    conn.commit()

    updated = q("SELECT * FROM students WHERE id=%s", (sid,), one=True)
    return jsonify({'success': True, 'new_paid': new_paid, 'student': serialize(updated)})

# ─────────────────────────────────────────────────────────
# ASSOCIATES
# ─────────────────────────────────────────────────────────

@app.route('/api/associates', methods=['GET'])
@login_required
def get_associates():
    rows = q("SELECT * FROM associates ORDER BY id DESC")
    return jsonify([serialize(r) for r in rows])

@app.route('/api/associates', methods=['POST'])
@login_required
def add_associate():
    d   = request.json or {}
    row = q_returning("""
        INSERT INTO associates (name,phone,student,work_done,amount,pay_date,pay_mode,utr,status,notes)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *
    """, (d.get('name'), d.get('phone'), d.get('student'), d.get('work_done'),
          d.get('amount', 0), d.get('pay_date') or None, d.get('pay_mode', 'Cash'),
          d.get('utr'), d.get('status', 'Paid'), d.get('notes')))
    return jsonify(serialize(row)), 201

@app.route('/api/associates/<int:aid>', methods=['DELETE'])
@login_required
def delete_associate(aid):
    q("DELETE FROM associates WHERE id=%s", (aid,), commit=True)
    return jsonify({'success': True})

# ─────────────────────────────────────────────────────────
# REFERENCES
# ─────────────────────────────────────────────────────────

@app.route('/api/references', methods=['GET'])
@login_required
def get_references():
    rows = q("SELECT * FROM references_ ORDER BY id DESC")
    return jsonify([serialize(r) for r in rows])

@app.route('/api/references', methods=['POST'])
@login_required
def add_reference():
    d   = request.json or {}
    row = q_returning("""
        INSERT INTO references_ (name,phone,student,university,amount,pay_date,pay_mode,utr,status,notes)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *
    """, (d.get('name'), d.get('phone'), d.get('student'), d.get('university'),
          d.get('amount', 0), d.get('pay_date') or None, d.get('pay_mode', 'Cash'),
          d.get('utr'), d.get('status', 'Paid'), d.get('notes')))
    return jsonify(serialize(row)), 201

@app.route('/api/references/<int:rid>', methods=['DELETE'])
@login_required
def delete_reference(rid):
    q("DELETE FROM references_ WHERE id=%s", (rid,), commit=True)
    return jsonify({'success': True})

# ─────────────────────────────────────────────────────────
# UNIVERSITIES
# ─────────────────────────────────────────────────────────

@app.route('/api/universities', methods=['GET'])
@login_required
def get_universities():
    rows = q("""
        SELECT u.id, u.name, u.state, u.color,
               COUNT(s.id)::int AS student_count
        FROM universities u
        LEFT JOIN students s ON s.university = u.name
        GROUP BY u.id, u.name, u.state, u.color
        ORDER BY u.name
    """)
    return jsonify(rows)

@app.route('/api/universities', methods=['POST'])
@login_required
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

@app.route('/api/universities/<int:uid>', methods=['DELETE'])
@login_required
@admin_required
def delete_university(uid):
    q("DELETE FROM universities WHERE id=%s", (uid,), commit=True)
    return jsonify({'success': True})

# ─────────────────────────────────────────────────────────
# DOCUMENTS
# ─────────────────────────────────────────────────────────

@app.route('/api/documents', methods=['GET'])
@login_required
def get_documents():
    rows = q("SELECT * FROM documents ORDER BY id DESC")
    return jsonify([serialize(r) for r in rows])

@app.route('/api/documents', methods=['POST'])
@login_required
def add_document():
    d = request.json or {}
    q_returning("""
        INSERT INTO documents (student,doc_type,university,issue_date,status,delivered_to)
        VALUES (%s,%s,%s,%s,%s,%s) RETURNING id
    """, (d.get('student'), d.get('doc_type'), d.get('university'),
          d.get('issue_date') or None, d.get('status', 'Delivered'), d.get('delivered_to')))
    return jsonify({'success': True}), 201

# ─────────────────────────────────────────────────────────
# USERS
# ─────────────────────────────────────────────────────────

@app.route('/api/users', methods=['GET'])
@login_required
def get_users():
    rows = q("SELECT id, username, full_name, role, created_at FROM users ORDER BY id")
    return jsonify([serialize(r) for r in rows])

@app.route('/api/users', methods=['POST'])
@login_required
@admin_required
def add_user():
    d = request.json or {}
    try:
        q_returning("""
            INSERT INTO users (username, password, full_name, role)
            VALUES (%s,%s,%s,%s) RETURNING id
        """, (d.get('username'), hash_pw(d.get('password', '')),
              d.get('full_name'), d.get('role', 'Staff')))
    except psycopg2.errors.UniqueViolation:
        get_db().rollback()
        return jsonify({'error': 'Username already exists'}), 409
    return jsonify({'success': True}), 201

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
# REPORTS (CSV download)
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
def report_students():
    data = q("SELECT * FROM students ORDER BY name")
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
def report_fees():
    data = q("SELECT * FROM students ORDER BY name")
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
def report_outstanding():
    data = q("SELECT * FROM students WHERE paid < total_fee ORDER BY (total_fee-paid) DESC")
    rows = [[s['name'],s['mobile'],s['university'],s['course'],
             float(s['total_fee']),float(s['paid']),
             float(s['total_fee'])-float(s['paid'])]
            for s in data]
    return csv_response(rows,
        ['Student','Mobile','University','Course','Total Fee','Paid','Outstanding'],
        f'Outstanding_{datetime.now().strftime("%Y%m%d")}.csv')

@app.route('/api/reports/assoc-ref')
@login_required
def report_assoc_ref():
    assocs = q("SELECT * FROM associates ORDER BY pay_date DESC")
    refs   = q("SELECT * FROM references_ ORDER BY pay_date DESC")
    rows   = []
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

# ─────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV', 'development') == 'development'
    print(f"\n{'='*50}")
    print(f"  Sky Eduworld — Management System")
    print(f"{'='*50}")
    print(f"  URL:    http://localhost:{port}")
    print(f"  Login:  admin / sky@2024")
    print(f"{'='*50}\n")
    app.run(host='0.0.0.0', port=port, debug=debug)
