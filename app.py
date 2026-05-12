"""
Sky Eduworld — Management System (PHASE 2 UPGRADE)
Backend: Flask + PostgreSQL

NEW IN PHASE 2:
- Activity / Audit logs
- Login history tracking
- Leads & follow-up system
- Installment-wise fee tracking
- Academic session management
- Notification system
- Advanced student profile
"""

import os, csv, io, hashlib, uuid
from datetime import datetime, timedelta, date
from functools import wraps

from flask import Flask, request, jsonify, send_from_directory, session, Response, g
from dotenv import load_dotenv
import psycopg2, psycopg2.extras

load_dotenv()
app = Flask(__name__, static_folder='static')
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-change-in-prod')
app.permanent_session_lifetime = timedelta(hours=8)
DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://postgres:password@localhost:5432/sky_eduworld')
UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED = {'png','jpg','jpeg','gif','webp','pdf','doc','docx'}

def allowed_file(fn): return '.' in fn and fn.rsplit('.',1)[1].lower() in ALLOWED

def get_db():
    if 'db' not in g:
        g.db = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        g.db.autocommit = False
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop('db', None)
    if db: db.close()

def q(sql, params=(), one=False, commit=False):
    conn = get_db(); cur = conn.cursor(); cur.execute(sql, params)
    if commit: conn.commit(); return cur.rowcount
    if one:
        row = cur.fetchone(); return dict(row) if row else None
    return [dict(r) for r in cur.fetchall()]

def q_ret(sql, params=()):
    conn = get_db(); cur = conn.cursor(); cur.execute(sql, params)
    row = cur.fetchone(); conn.commit(); return dict(row) if row else None

def hash_pw(pw): return hashlib.sha256(pw.encode()).hexdigest()

def serialize(row):
    out = {}
    for k, v in row.items():
        if hasattr(v,'isoformat'): out[k] = v.isoformat()
        elif hasattr(v,'__float__'): out[k] = float(v)
        else: out[k] = v
    return out

def log_action(action, module, record_id=None, detail=None):
    try:
        uid = session.get('user_id')
        if not uid: return
        q("INSERT INTO activity_logs (user_id,action_type,module_name,record_id,detail,ip_address) VALUES (%s,%s,%s,%s,%s,%s)",
          (uid, action, module, record_id, detail, request.remote_addr or 'unknown'), commit=True)
    except Exception: pass

def notify_user(uid, title, msg, ntype='info', link=None):
    try:
        q("INSERT INTO notifications (user_id,title,message,type,link) VALUES (%s,%s,%s,%s,%s)",
          (uid, title, msg, ntype, link), commit=True)
    except Exception: pass

def is_super_admin(): return session.get('role') == 'Super Admin'
def is_admin(): return session.get('role') in ('Admin','Super Admin')

def get_user_perms(user_id):
    if is_super_admin():
        return {p:True for p in ['can_add_student','can_edit_student','can_delete_student',
            'can_view_payments','can_add_payment','can_view_associates','can_manage_associates',
            'can_view_references','can_manage_references','can_view_documents','can_upload_document',
            'can_view_reports','can_manage_universities','can_view_all_students',
            'can_manage_leads','can_view_audit_logs']}
    perms = q("SELECT * FROM user_permissions WHERE user_id=%s", (user_id,), one=True)
    if not perms:
        return {'can_add_student':True,'can_edit_student':True,'can_delete_student':False,
                'can_view_payments':True,'can_add_payment':True,'can_view_associates':False,
                'can_manage_associates':False,'can_view_references':False,'can_manage_references':False,
                'can_view_documents':True,'can_upload_document':True,'can_view_reports':False,
                'can_manage_universities':False,'can_view_all_students':False,
                'can_manage_leads':False,'can_view_audit_logs':False}
    return dict(perms)

def get_user_univs(user_id):
    if is_super_admin(): return []
    rows = q("SELECT u.name FROM user_universities uu JOIN universities u ON u.id=uu.university_id WHERE uu.user_id=%s", (user_id,))
    return [r['name'] for r in rows]

def student_filter(user_id, alias=''):
    p = f"{alias}." if alias else ""
    perms = get_user_perms(user_id)
    clauses, params = [], []
    if not perms.get('can_view_all_students') and not is_super_admin():
        clauses.append(f"{p}created_by = %s"); params.append(user_id)
    univs = get_user_univs(user_id)
    if univs:
        ph = ','.join(['%s']*len(univs)); clauses.append(f"{p}university IN ({ph})"); params.extend(univs)
    sql = (' AND ' + ' AND '.join(clauses)) if clauses else ''
    return sql, params

def get_active_session():
    return q("SELECT * FROM academic_sessions WHERE is_active=TRUE ORDER BY id DESC LIMIT 1", one=True)

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error':'Not authenticated','redirect':'/'}), 401
        try:
            stored = q("SELECT session_token,is_active FROM users WHERE id=%s", (session['user_id'],), one=True)
            if stored:
                if stored.get('session_token') != session.get('session_token'):
                    session.clear(); return jsonify({'error':'Session expired','redirect':'/'}), 401
                if not stored.get('is_active'):
                    session.clear(); return jsonify({'error':'Account disabled','redirect':'/'}), 401
        except Exception: pass
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_admin(): return jsonify({'error':'Admin required'}), 403
        return f(*args, **kwargs)
    return decorated

def super_admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_super_admin(): return jsonify({'error':'Super Admin required'}), 403
        return f(*args, **kwargs)
    return decorated

def require_perm(perm):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not get_user_perms(session.get('user_id')).get(perm):
                return jsonify({'error':f'Permission denied: {perm}'}), 403
            return f(*args, **kwargs)
        return decorated
    return decorator

def init_db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (id SERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, full_name TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'Staff', is_active BOOLEAN DEFAULT TRUE, session_token TEXT, failed_logins INTEGER DEFAULT 0, last_login TIMESTAMP, created_at TIMESTAMP DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS user_permissions (id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, can_add_student BOOLEAN DEFAULT TRUE, can_edit_student BOOLEAN DEFAULT TRUE, can_delete_student BOOLEAN DEFAULT FALSE, can_view_payments BOOLEAN DEFAULT TRUE, can_add_payment BOOLEAN DEFAULT TRUE, can_view_associates BOOLEAN DEFAULT FALSE, can_manage_associates BOOLEAN DEFAULT FALSE, can_view_references BOOLEAN DEFAULT FALSE, can_manage_references BOOLEAN DEFAULT FALSE, can_view_documents BOOLEAN DEFAULT TRUE, can_upload_document BOOLEAN DEFAULT TRUE, can_view_reports BOOLEAN DEFAULT FALSE, can_manage_universities BOOLEAN DEFAULT FALSE, can_view_all_students BOOLEAN DEFAULT FALSE, can_manage_leads BOOLEAN DEFAULT FALSE, can_view_audit_logs BOOLEAN DEFAULT FALSE, UNIQUE(user_id));
    CREATE TABLE IF NOT EXISTS universities (id SERIAL PRIMARY KEY, name TEXT UNIQUE NOT NULL, state TEXT, color TEXT DEFAULT '#1A6CF6', is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS user_universities (id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, university_id INTEGER NOT NULL REFERENCES universities(id) ON DELETE CASCADE, UNIQUE(user_id,university_id));
    CREATE TABLE IF NOT EXISTS academic_sessions (id SERIAL PRIMARY KEY, name TEXT NOT NULL, start_date DATE, end_date DATE, is_active BOOLEAN DEFAULT FALSE, created_by INTEGER REFERENCES users(id), created_at TIMESTAMP DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS students (id SERIAL PRIMARY KEY, created_by INTEGER REFERENCES users(id), session_id INTEGER REFERENCES academic_sessions(id), name TEXT NOT NULL, father TEXT, mother TEXT, dob DATE, gender TEXT, mobile TEXT, email TEXT, aadhar TEXT, address TEXT, course TEXT, university TEXT, batch TEXT, enroll_no TEXT, roll_no TEXT, adm_date DATE, remarks TEXT, total_fee NUMERIC(12,2) DEFAULT 0, paid NUMERIC(12,2) DEFAULT 0, univ_fee NUMERIC(12,2) DEFAULT 0, pay_mode TEXT, utr TEXT, doc_notes TEXT, status TEXT DEFAULT 'Active', photo_path TEXT, created_at TIMESTAMP DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS fee_installments (id SERIAL PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE, created_by INTEGER REFERENCES users(id), amount NUMERIC(12,2) NOT NULL, due_date DATE, paid_date DATE, status TEXT DEFAULT 'Pending', remarks TEXT, created_at TIMESTAMP DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS fee_payments (id SERIAL PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE, recorded_by INTEGER REFERENCES users(id), installment_id INTEGER REFERENCES fee_installments(id), amount NUMERIC(12,2) NOT NULL, fee_type TEXT, pay_mode TEXT, ref_no TEXT, pay_date DATE, remarks TEXT, created_at TIMESTAMP DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS associates (id SERIAL PRIMARY KEY, created_by INTEGER REFERENCES users(id), name TEXT NOT NULL, phone TEXT, student TEXT, work_done TEXT, amount NUMERIC(12,2) DEFAULT 0, pay_date DATE, pay_mode TEXT, utr TEXT, status TEXT DEFAULT 'Paid', notes TEXT, created_at TIMESTAMP DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS references_ (id SERIAL PRIMARY KEY, created_by INTEGER REFERENCES users(id), name TEXT NOT NULL, phone TEXT, student TEXT, university TEXT, amount NUMERIC(12,2) DEFAULT 0, pay_date DATE, pay_mode TEXT, utr TEXT, status TEXT DEFAULT 'Paid', notes TEXT, created_at TIMESTAMP DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS documents (id SERIAL PRIMARY KEY, student_id INTEGER REFERENCES students(id) ON DELETE SET NULL, student TEXT NOT NULL, doc_type TEXT NOT NULL, university TEXT, issue_date DATE, status TEXT DEFAULT 'Delivered', delivered_to TEXT, file_path TEXT, file_name TEXT, uploaded_by INTEGER REFERENCES users(id), created_at TIMESTAMP DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS student_photos (id SERIAL PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE, file_path TEXT NOT NULL, file_name TEXT, uploaded_by INTEGER REFERENCES users(id), uploaded_at TIMESTAMP DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS leads (id SERIAL PRIMARY KEY, created_by INTEGER REFERENCES users(id), name TEXT NOT NULL, mobile TEXT, email TEXT, course TEXT, university TEXT, source TEXT DEFAULT 'Walk-in', status TEXT DEFAULT 'New', remarks TEXT, follow_up_date DATE, converted_to INTEGER REFERENCES students(id), created_at TIMESTAMP DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS follow_ups (id SERIAL PRIMARY KEY, created_by INTEGER REFERENCES users(id), student_id INTEGER REFERENCES students(id) ON DELETE CASCADE, lead_id INTEGER REFERENCES leads(id) ON DELETE CASCADE, note TEXT NOT NULL, follow_type TEXT DEFAULT 'Call', next_date DATE, created_at TIMESTAMP DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS activity_logs (id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id), action_type TEXT NOT NULL, module_name TEXT, record_id INTEGER, detail TEXT, ip_address TEXT, created_at TIMESTAMP DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS login_history (id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id), username TEXT, status TEXT DEFAULT 'Success', ip_address TEXT, created_at TIMESTAMP DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS notifications (id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id), title TEXT NOT NULL, message TEXT, type TEXT DEFAULT 'info', is_read BOOLEAN DEFAULT FALSE, link TEXT, created_at TIMESTAMP DEFAULT NOW());
    """)
    cur.execute("SELECT id FROM users WHERE username='admin'")
    if not cur.fetchone():
        cur.execute("INSERT INTO users (username,password,full_name,role) VALUES (%s,%s,%s,%s)",
                    ('admin', hash_pw('sky@2024'), 'Admin', 'Super Admin'))
    cur.execute("SELECT id FROM academic_sessions LIMIT 1")
    if not cur.fetchone():
        cur.execute("INSERT INTO academic_sessions (name,start_date,end_date,is_active) VALUES (%s,%s,%s,%s)",
                    ('2025-26','2025-07-01','2026-06-30',True))
    for u in [('Sikkim Alpine University','Sikkim','#3B82F6'),('Sunrise University','Rajasthan','#10B981'),
              ('Glocal University','Uttar Pradesh','#F5A623'),('YBN University','Jharkhand','#8B5CF6'),
              ('Nirwan University','Rajasthan','#F43F5E'),('Manglaytan University','Uttar Pradesh','#06B6D4'),
              ('Monad University','Uttar Pradesh','#84CC16'),('Calorax Teachers University','Rajasthan','#EF4444'),
              ('IEC University','Himachal Pradesh','#F97316'),('Pratap University','Rajasthan','#EC4899'),
              ('Arni University','Himachal Pradesh','#14B8A6'),('Saroj University','Uttar Pradesh','#A855F7'),
              ('Shridhar University','Rajasthan','#0EA5E9'),('Madhyanchal Professional University','Madhya Pradesh','#D97706'),
              ('Mansarovar Global University','Madhya Pradesh','#7C3AED'),('Mats University','Chhattisgarh','#059669'),
              ('North Eastern Christian University','Nagaland','#64748B'),('Sabarmati University','Gujarat','#DC2626'),
              ('P K University','Rajasthan','#0891B2')]:
        cur.execute("INSERT INTO universities (name,state,color) VALUES (%s,%s,%s) ON CONFLICT (name) DO NOTHING", u)
    for m in ["ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE",
              "ALTER TABLE users ADD COLUMN IF NOT EXISTS session_token TEXT",
              "ALTER TABLE users ADD COLUMN IF NOT EXISTS failed_logins INTEGER DEFAULT 0",
              "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login TIMESTAMP",
              "ALTER TABLE students ADD COLUMN IF NOT EXISTS created_by INTEGER",
              "ALTER TABLE students ADD COLUMN IF NOT EXISTS photo_path TEXT",
              "ALTER TABLE students ADD COLUMN IF NOT EXISTS session_id INTEGER",
              "ALTER TABLE fee_payments ADD COLUMN IF NOT EXISTS recorded_by INTEGER",
              "ALTER TABLE fee_payments ADD COLUMN IF NOT EXISTS installment_id INTEGER",
              "ALTER TABLE associates ADD COLUMN IF NOT EXISTS created_by INTEGER",
              "ALTER TABLE references_ ADD COLUMN IF NOT EXISTS created_by INTEGER",
              "ALTER TABLE user_permissions ADD COLUMN IF NOT EXISTS can_manage_leads BOOLEAN DEFAULT FALSE",
              "ALTER TABLE user_permissions ADD COLUMN IF NOT EXISTS can_view_audit_logs BOOLEAN DEFAULT FALSE",
              "UPDATE users SET is_active=TRUE WHERE is_active IS NULL"]:
        try: cur.execute(m)
        except Exception: conn.rollback()
    conn.commit(); conn.close(); print("✅ Phase 2 DB ready.")

# STATIC
@app.route('/') 
def index(): return send_from_directory('static','index.html')

@app.route('/uploads/<path:filename>')
@login_required
def serve_upload(filename): return send_from_directory(UPLOAD_FOLDER, filename)

# AUTH
@app.route('/api/login', methods=['POST'])
def login():
    d = request.json or {}
    ip = request.remote_addr or 'unknown'
    user = q("SELECT * FROM users WHERE username=%s", (d.get('username',''),), one=True)
    if not user or user['password'] != hash_pw(d.get('password','')):
        try:
            q("INSERT INTO login_history (user_id,username,status,ip_address) VALUES (%s,%s,%s,%s)",
              (user['id'] if user else None, d.get('username',''), 'Failed', ip), commit=True)
            if user: q("UPDATE users SET failed_logins=COALESCE(failed_logins,0)+1 WHERE id=%s", (user['id'],), commit=True)
        except Exception: pass
        return jsonify({'error':'Invalid username or password'}), 401
    if not user.get('is_active',True):
        return jsonify({'error':'Account disabled. Contact admin.'}), 401
    token = str(uuid.uuid4())
    q("UPDATE users SET session_token=%s, failed_logins=0, last_login=NOW() WHERE id=%s", (token, user['id']), commit=True)
    try:
        q("INSERT INTO login_history (user_id,username,status,ip_address) VALUES (%s,%s,%s,%s)",
          (user['id'], user['username'], 'Success', ip), commit=True)
    except Exception: pass
    session.permanent = True
    session['user_id'] = user['id']; session['username'] = user['username']
    session['role'] = user['role']; session['session_token'] = token
    perms = get_user_perms(user['id']); univs = get_user_univs(user['id'])
    active_sess = get_active_session()
    return jsonify({'success':True,'user':{'id':user['id'],'username':user['username'],
        'full_name':user['full_name'],'role':user['role'],'permissions':perms,
        'assigned_universities':univs,'active_session':serialize(active_sess) if active_sess else None}})

@app.route('/api/logout', methods=['POST'])
def logout():
    if 'user_id' in session:
        log_action('Logout','Auth')
        q("UPDATE users SET session_token=NULL WHERE id=%s", (session['user_id'],), commit=True)
    session.clear(); return jsonify({'success':True})

@app.route('/api/me')
@login_required
def me():
    user = q("SELECT id,username,full_name,role,last_login FROM users WHERE id=%s", (session['user_id'],), one=True)
    perms = get_user_perms(session['user_id']); univs = get_user_univs(session['user_id'])
    unread = q("SELECT COUNT(*) AS c FROM notifications WHERE user_id=%s AND is_read=FALSE", (session['user_id'],), one=True)
    active_sess = get_active_session()
    return jsonify({**serialize(user),'permissions':perms,'assigned_universities':univs,
                    'unread_notifications':unread['c'] if unread else 0,
                    'active_session':serialize(active_sess) if active_sess else None})

# DASHBOARD
@app.route('/api/dashboard')
@login_required
def dashboard():
    uid = session['user_id']; fs, fp = student_filter(uid, 's')
    stats = q(f"SELECT COUNT(*) AS student_count, COALESCE(SUM(paid),0) AS total_collected, COALESCE(SUM(total_fee-paid),0) AS outstanding, COALESCE(SUM(univ_fee),0) AS univ_fee_total FROM students WHERE TRUE {fs}", fp, one=True)
    perms = get_user_perms(uid)
    assoc_total = ref_total = 0
    if perms.get('can_view_associates') or is_super_admin():
        a = q("SELECT COALESCE(SUM(amount),0) AS t FROM associates" + ("" if is_super_admin() else " WHERE created_by=%s"), () if is_super_admin() else (uid,), one=True)
        assoc_total = float(a['t'])
    if perms.get('can_view_references') or is_super_admin():
        r = q("SELECT COALESCE(SUM(amount),0) AS t FROM references_" + ("" if is_super_admin() else " WHERE created_by=%s"), () if is_super_admin() else (uid,), one=True)
        ref_total = float(r['t'])
    stats['assoc_ref_paid'] = assoc_total + ref_total
    today_col = q(f"SELECT COALESCE(SUM(fp.amount),0) AS t FROM fee_payments fp JOIN students s ON s.id=fp.student_id WHERE DATE(fp.created_at)=CURRENT_DATE {fs}", fp, one=True)
    stats['today_collection'] = float(today_col['t'])
    try:
        pending_inst = q(f"SELECT COUNT(*) AS c, COALESCE(SUM(fi.amount),0) AS total FROM fee_installments fi JOIN students s ON s.id=fi.student_id WHERE fi.status='Pending' AND fi.due_date <= CURRENT_DATE {fs}", fp, one=True)
        stats['overdue_count'] = pending_inst['c']; stats['overdue_amount'] = float(pending_inst['total'])
    except Exception: stats['overdue_count'] = 0; stats['overdue_amount'] = 0
    try:
        leads_count = q("SELECT COUNT(*) AS c FROM leads WHERE status != 'Converted'" + ("" if is_super_admin() else " AND created_by=%s"), () if is_super_admin() else (uid,), one=True)
        stats['active_leads'] = leads_count['c']
    except Exception: stats['active_leads'] = 0
    recent = q(f"SELECT s.*, u.full_name AS created_by_name FROM students s LEFT JOIN users u ON u.id=s.created_by WHERE TRUE {fs} ORDER BY s.id DESC LIMIT 6", fp)
    fee_tracker = q(f"SELECT * FROM students WHERE TRUE {fs} AND total_fee > paid ORDER BY (total_fee-paid) DESC LIMIT 5", fp)
    if is_super_admin():
        univs = q("SELECT u.name, u.color, COUNT(s.id) AS count FROM universities u LEFT JOIN students s ON s.university=u.name GROUP BY u.name,u.color ORDER BY count DESC LIMIT 10")
    else:
        univs = q("SELECT u.name, u.color, COUNT(s.id) AS count FROM universities u LEFT JOIN students s ON s.university=u.name AND s.created_by=%s GROUP BY u.name,u.color ORDER BY count DESC LIMIT 10", (uid,))
    user_summary = []
    if is_super_admin():
        user_summary = [serialize(r) for r in q("SELECT u.id, u.full_name, u.role, u.last_login, COUNT(s.id) AS student_count, COALESCE(SUM(s.paid),0) AS total_collected, COALESCE(SUM(s.total_fee-s.paid),0) AS outstanding FROM users u LEFT JOIN students s ON s.created_by=u.id GROUP BY u.id,u.full_name,u.role,u.last_login ORDER BY student_count DESC")]
    try:
        followups = q("SELECT f.*, s.name AS student_name FROM follow_ups f LEFT JOIN students s ON s.id=f.student_id WHERE f.next_date >= CURRENT_DATE AND f.next_date <= CURRENT_DATE+7 AND f.created_by=%s ORDER BY f.next_date LIMIT 5", (uid,))
    except Exception: followups = []
    return jsonify({'stats':{k:float(v) if isinstance(v,(int,float)) else v for k,v in stats.items()},
                    'recent':[serialize(r) for r in recent],'fee_tracker':[serialize(r) for r in fee_tracker],
                    'universities':univs,'user_summary':user_summary,'permissions':perms,
                    'upcoming_followups':[serialize(r) for r in followups]})

# STUDENTS
@app.route('/api/students', methods=['GET'])
@login_required
def get_students():
    uid = session['user_id']; search = request.args.get('q','').strip()
    univ = request.args.get('university',''); status = request.args.get('status','')
    target_user = request.args.get('user_id'); sess_id = request.args.get('session_id')
    fs, fp = student_filter(uid, 's')
    sql = f"SELECT s.*, u.full_name AS created_by_name, ac.name AS session_name FROM students s LEFT JOIN users u ON u.id=s.created_by LEFT JOIN academic_sessions ac ON ac.id=s.session_id WHERE TRUE {fs}"
    params = list(fp)
    if is_super_admin() and target_user: sql += " AND s.created_by=%s"; params.append(int(target_user))
    if search:
        sql += " AND (s.name ILIKE %s OR s.father ILIKE %s OR s.mobile ILIKE %s OR s.course ILIKE %s OR s.university ILIKE %s OR s.enroll_no ILIKE %s)"
        p = f'%{search}%'; params += [p,p,p,p,p,p]
    if univ: sql += " AND s.university=%s"; params.append(univ)
    if status: sql += " AND s.status=%s"; params.append(status)
    if sess_id: sql += " AND s.session_id=%s"; params.append(int(sess_id))
    sql += " ORDER BY s.id DESC"
    return jsonify([serialize(r) for r in q(sql, params)])

@app.route('/api/students', methods=['POST'])
@login_required
@require_perm('can_add_student')
def add_student():
    d = request.json or {}; uid = session['user_id']
    univs = get_user_univs(uid)
    if univs and d.get('university') not in univs: return jsonify({'error':'Not assigned to this university'}), 403
    active_sess = get_active_session()
    row = q_ret("""INSERT INTO students (created_by,session_id,name,father,mother,dob,gender,mobile,email,aadhar,address,course,university,batch,enroll_no,roll_no,adm_date,remarks,total_fee,paid,univ_fee,pay_mode,utr,doc_notes,status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
               (uid, active_sess['id'] if active_sess else None, d.get('name'), d.get('father'), d.get('mother'),
                d.get('dob') or None, d.get('gender'), d.get('mobile'), d.get('email'), d.get('aadhar'), d.get('address'),
                d.get('course'), d.get('university'), d.get('batch'), d.get('enroll_no'), d.get('roll_no'),
                d.get('adm_date') or None, d.get('remarks'), d.get('total_fee',0), d.get('paid',0),
                d.get('univ_fee',0), d.get('pay_mode'), d.get('utr'), d.get('doc_notes'), 'Active'))
    paid = float(d.get('paid',0) or 0)
    if paid > 0 and row:
        conn = get_db(); cur = conn.cursor()
        cur.execute("INSERT INTO fee_payments (student_id,recorded_by,amount,fee_type,pay_mode,ref_no,pay_date) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (row['id'],uid,paid,'Initial Payment',d.get('pay_mode'),d.get('utr'),d.get('adm_date') or None))
        conn.commit()
    log_action('Add','Student',row['id'] if row else None, d.get('name'))
    return jsonify(serialize(row)), 201

@app.route('/api/students/<int:sid>', methods=['GET'])
@login_required
def get_student(sid):
    uid = session['user_id']; fs, fp = student_filter(uid,'s')
    row = q(f"SELECT s.* FROM students s WHERE s.id=%s {fs}", [sid]+list(fp), one=True)
    if not row: return jsonify({'error':'Not found or access denied'}), 404
    photo = q("SELECT * FROM student_photos WHERE student_id=%s ORDER BY id DESC LIMIT 1", (sid,), one=True)
    payments = q("SELECT fp.*, u.full_name AS by_name FROM fee_payments fp LEFT JOIN users u ON u.id=fp.recorded_by WHERE fp.student_id=%s ORDER BY fp.id DESC", (sid,))
    installments = q("SELECT * FROM fee_installments WHERE student_id=%s ORDER BY due_date", (sid,))
    followups = q("SELECT f.*, u.full_name AS by_name FROM follow_ups f LEFT JOIN users u ON u.id=f.created_by WHERE f.student_id=%s ORDER BY f.created_at DESC", (sid,))
    docs = q("SELECT * FROM documents WHERE student_id=%s ORDER BY id DESC", (sid,))
    row['photo'] = serialize(photo) if photo else None
    row['payments'] = [serialize(r) for r in payments]
    row['installments'] = [serialize(r) for r in installments]
    row['followups'] = [serialize(r) for r in followups]
    row['documents'] = [serialize(r) for r in docs]
    return jsonify(serialize(row))

@app.route('/api/students/<int:sid>', methods=['PUT'])
@login_required
@require_perm('can_edit_student')
def update_student(sid):
    uid = session['user_id']; fs, fp = student_filter(uid)
    if not q(f"SELECT id FROM students WHERE id=%s {fs}", [sid]+list(fp), one=True):
        return jsonify({'error':'Not found or access denied'}), 404
    d = request.json or {}
    row = q_ret("""UPDATE students SET name=%s,father=%s,mother=%s,dob=%s,gender=%s,mobile=%s,email=%s,aadhar=%s,address=%s,course=%s,university=%s,batch=%s,enroll_no=%s,roll_no=%s,adm_date=%s,remarks=%s,total_fee=%s,univ_fee=%s,pay_mode=%s,utr=%s,doc_notes=%s,status=%s WHERE id=%s RETURNING *""",
               (d.get('name'),d.get('father'),d.get('mother'),d.get('dob') or None,d.get('gender'),d.get('mobile'),d.get('email'),d.get('aadhar'),d.get('address'),d.get('course'),d.get('university'),d.get('batch'),d.get('enroll_no'),d.get('roll_no'),d.get('adm_date') or None,d.get('remarks'),d.get('total_fee',0),d.get('univ_fee',0),d.get('pay_mode'),d.get('utr'),d.get('doc_notes'),d.get('status','Active'),sid))
    log_action('Edit','Student',sid,d.get('name'))
    return jsonify(serialize(row))

@app.route('/api/students/<int:sid>', methods=['DELETE'])
@login_required
@require_perm('can_delete_student')
def delete_student(sid):
    uid = session['user_id']; fs, fp = student_filter(uid)
    if not q(f"SELECT id FROM students WHERE id=%s {fs}", [sid]+list(fp), one=True):
        return jsonify({'error':'Not found or access denied'}), 404
    log_action('Delete','Student',sid)
    q("DELETE FROM students WHERE id=%s", (sid,), commit=True)
    return jsonify({'success':True})

@app.route('/api/students/<int:sid>/photo', methods=['POST'])
@login_required
def upload_photo(sid):
    if 'photo' not in request.files: return jsonify({'error':'No photo'}), 400
    file = request.files['photo']
    if not allowed_file(file.filename): return jsonify({'error':'Invalid type'}), 400
    ext = file.filename.rsplit('.',1)[1].lower()
    filename = f"student_{sid}_photo_{uuid.uuid4().hex[:8]}.{ext}"
    file.save(os.path.join(UPLOAD_FOLDER, filename))
    q("UPDATE students SET photo_path=%s WHERE id=%s", (filename,sid), commit=True)
    q_ret("INSERT INTO student_photos (student_id,file_path,file_name,uploaded_by) VALUES (%s,%s,%s,%s) RETURNING id",
          (sid, filename, file.filename, session['user_id']))
    return jsonify({'success':True,'filename':filename,'url':f'/uploads/{filename}'})

# FEE PAYMENTS
@app.route('/api/students/<int:sid>/payments', methods=['GET'])
@login_required
def get_payments(sid):
    rows = q("SELECT fp.*, u.full_name AS by_name FROM fee_payments fp LEFT JOIN users u ON u.id=fp.recorded_by WHERE fp.student_id=%s ORDER BY fp.id DESC", (sid,))
    return jsonify([serialize(r) for r in rows])

@app.route('/api/students/<int:sid>/payments', methods=['POST'])
@login_required
@require_perm('can_add_payment')
def add_payment(sid):
    uid = session['user_id']; d = request.json or {}; amount = float(d.get('amount',0) or 0)
    fs, fp = student_filter(uid)
    student = q(f"SELECT * FROM students WHERE id=%s {fs}", [sid]+list(fp), one=True)
    if not student: return jsonify({'error':'Not found or access denied'}), 404
    new_paid = min(float(student['paid']) + amount, float(student['total_fee']))
    conn = get_db(); cur = conn.cursor()
    cur.execute("UPDATE students SET paid=%s WHERE id=%s", (new_paid,sid))
    cur.execute("INSERT INTO fee_payments (student_id,recorded_by,amount,fee_type,pay_mode,ref_no,pay_date,remarks) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (sid,uid,amount,d.get('fee_type','Tuition Fee'),d.get('pay_mode','Cash'),d.get('ref_no',''),d.get('pay_date') or date.today().isoformat(),d.get('remarks','')))
    if d.get('installment_id'):
        cur.execute("UPDATE fee_installments SET status='Paid', paid_date=CURRENT_DATE WHERE id=%s", (d['installment_id'],))
    conn.commit()
    log_action('Payment','Fee',sid,f"Rs{amount}")
    if new_paid >= float(student['total_fee']): notify_user(uid,'Fee Cleared!',f"{student['name']} ka poora fee jama ho gaya.",'success')
    updated = q("SELECT * FROM students WHERE id=%s", (sid,), one=True)
    return jsonify({'success':True,'new_paid':new_paid,'student':serialize(updated)})

# INSTALLMENTS
@app.route('/api/students/<int:sid>/installments', methods=['GET'])
@login_required
def get_installments(sid):
    return jsonify([serialize(r) for r in q("SELECT * FROM fee_installments WHERE student_id=%s ORDER BY due_date", (sid,))])

@app.route('/api/students/<int:sid>/installments', methods=['POST'])
@login_required
def add_installment(sid):
    d = request.json or {}
    row = q_ret("INSERT INTO fee_installments (student_id,created_by,amount,due_date,remarks) VALUES (%s,%s,%s,%s,%s) RETURNING *",
                (sid, session['user_id'], d.get('amount',0), d.get('due_date') or None, d.get('remarks')))
    return jsonify(serialize(row)), 201

@app.route('/api/installments/overdue')
@login_required
def overdue_installments():
    uid = session['user_id']; fs, fp = student_filter(uid,'s')
    rows = q(f"SELECT fi.*, s.name AS student_name, s.mobile, s.university, s.course FROM fee_installments fi JOIN students s ON s.id=fi.student_id WHERE fi.status='Pending' AND fi.due_date <= CURRENT_DATE {fs} ORDER BY fi.due_date", fp)
    return jsonify([serialize(r) for r in rows])

# FOLLOW-UPS
@app.route('/api/students/<int:sid>/followups', methods=['GET'])
@login_required
def get_followups(sid):
    rows = q("SELECT f.*, u.full_name AS by_name FROM follow_ups f LEFT JOIN users u ON u.id=f.created_by WHERE f.student_id=%s ORDER BY f.created_at DESC", (sid,))
    return jsonify([serialize(r) for r in rows])

@app.route('/api/students/<int:sid>/followups', methods=['POST'])
@login_required
def add_followup(sid):
    d = request.json or {}
    row = q_ret("INSERT INTO follow_ups (created_by,student_id,note,follow_type,next_date) VALUES (%s,%s,%s,%s,%s) RETURNING *",
                (session['user_id'],sid,d.get('note'),d.get('follow_type','Call'),d.get('next_date') or None))
    return jsonify(serialize(row)), 201

@app.route('/api/followups/upcoming')
@login_required
def upcoming_followups():
    uid = session['user_id']; days = int(request.args.get('days',7))
    if is_super_admin():
        rows = q("SELECT f.*, s.name AS student_name, l.name AS lead_name, u.full_name AS by_name FROM follow_ups f LEFT JOIN students s ON s.id=f.student_id LEFT JOIN leads l ON l.id=f.lead_id LEFT JOIN users u ON u.id=f.created_by WHERE f.next_date BETWEEN CURRENT_DATE AND CURRENT_DATE+%s ORDER BY f.next_date", (days,))
    else:
        rows = q("SELECT f.*, s.name AS student_name, l.name AS lead_name FROM follow_ups f LEFT JOIN students s ON s.id=f.student_id LEFT JOIN leads l ON l.id=f.lead_id WHERE f.created_by=%s AND f.next_date BETWEEN CURRENT_DATE AND CURRENT_DATE+%s ORDER BY f.next_date", (uid,days))
    return jsonify([serialize(r) for r in rows])

# LEADS
@app.route('/api/leads', methods=['GET'])
@login_required
def get_leads():
    uid = session['user_id']; status = request.args.get('status','')
    if is_super_admin():
        sql = "SELECT l.*, u.full_name AS by_name FROM leads l LEFT JOIN users u ON u.id=l.created_by WHERE TRUE"; params = []
    else:
        sql = "SELECT l.*, u.full_name AS by_name FROM leads l LEFT JOIN users u ON u.id=l.created_by WHERE l.created_by=%s"; params = [uid]
    if status: sql += " AND l.status=%s"; params.append(status)
    return jsonify([serialize(r) for r in q(sql+' ORDER BY l.id DESC', params)])

@app.route('/api/leads', methods=['POST'])
@login_required
def add_lead():
    d = request.json or {}
    row = q_ret("INSERT INTO leads (created_by,name,mobile,email,course,university,source,status,remarks,follow_up_date) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                (session['user_id'],d.get('name'),d.get('mobile'),d.get('email'),d.get('course'),d.get('university'),d.get('source','Walk-in'),d.get('status','New'),d.get('remarks'),d.get('follow_up_date') or None))
    log_action('Add','Lead',row['id'] if row else None, d.get('name'))
    return jsonify(serialize(row)), 201

@app.route('/api/leads/<int:lid>', methods=['PUT'])
@login_required
def update_lead(lid):
    d = request.json or {}
    row = q_ret("UPDATE leads SET name=%s,mobile=%s,email=%s,course=%s,university=%s,source=%s,status=%s,remarks=%s,follow_up_date=%s WHERE id=%s RETURNING *",
                (d.get('name'),d.get('mobile'),d.get('email'),d.get('course'),d.get('university'),d.get('source','Walk-in'),d.get('status','New'),d.get('remarks'),d.get('follow_up_date') or None, lid))
    return jsonify(serialize(row))

@app.route('/api/leads/<int:lid>/convert', methods=['POST'])
@login_required
def convert_lead(lid):
    lead = q("SELECT * FROM leads WHERE id=%s", (lid,), one=True)
    if not lead: return jsonify({'error':'Lead not found'}), 404
    active_sess = get_active_session()
    row = q_ret("INSERT INTO students (created_by,session_id,name,mobile,email,course,university,status,adm_date) VALUES (%s,%s,%s,%s,%s,%s,%s,'Active',CURRENT_DATE) RETURNING *",
                (session['user_id'], active_sess['id'] if active_sess else None, lead['name'],lead['mobile'],lead['email'],lead['course'],lead['university']))
    if row:
        q("UPDATE leads SET status='Converted', converted_to=%s WHERE id=%s", (row['id'],lid), commit=True)
        log_action('Convert','Lead',lid,lead['name'])
    return jsonify({'success':True,'student_id':row['id'] if row else None})

@app.route('/api/leads/<int:lid>/followups', methods=['POST'])
@login_required
def add_lead_followup(lid):
    d = request.json or {}
    row = q_ret("INSERT INTO follow_ups (created_by,lead_id,note,follow_type,next_date) VALUES (%s,%s,%s,%s,%s) RETURNING *",
                (session['user_id'],lid,d.get('note'),d.get('follow_type','Call'),d.get('next_date') or None))
    if d.get('next_date'): q("UPDATE leads SET follow_up_date=%s WHERE id=%s", (d['next_date'],lid), commit=True)
    return jsonify(serialize(row)), 201

@app.route('/api/leads/<int:lid>', methods=['DELETE'])
@login_required
def delete_lead(lid):
    q("DELETE FROM leads WHERE id=%s", (lid,), commit=True); return jsonify({'success':True})

# ASSOCIATES
@app.route('/api/associates', methods=['GET'])
@login_required
@require_perm('can_view_associates')
def get_associates():
    uid = session['user_id']
    if is_super_admin(): rows = q("SELECT a.*, u.full_name AS by_name FROM associates a LEFT JOIN users u ON u.id=a.created_by ORDER BY a.id DESC")
    else: rows = q("SELECT a.*, u.full_name AS by_name FROM associates a LEFT JOIN users u ON u.id=a.created_by WHERE a.created_by=%s ORDER BY a.id DESC", (uid,))
    return jsonify([serialize(r) for r in rows])

@app.route('/api/associates', methods=['POST'])
@login_required
@require_perm('can_manage_associates')
def add_associate():
    d = request.json or {}
    row = q_ret("INSERT INTO associates (created_by,name,phone,student,work_done,amount,pay_date,pay_mode,utr,status,notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                (session['user_id'],d.get('name'),d.get('phone'),d.get('student'),d.get('work_done'),d.get('amount',0),d.get('pay_date') or None,d.get('pay_mode','Cash'),d.get('utr'),d.get('status','Paid'),d.get('notes')))
    return jsonify(serialize(row)), 201

@app.route('/api/associates/<int:aid>', methods=['DELETE'])
@login_required
@require_perm('can_manage_associates')
def delete_associate(aid):
    if not is_super_admin():
        if not q("SELECT id FROM associates WHERE id=%s AND created_by=%s", (aid,session['user_id']), one=True): return jsonify({'error':'Access denied'}), 403
    q("DELETE FROM associates WHERE id=%s", (aid,), commit=True); return jsonify({'success':True})

# REFERENCES
@app.route('/api/references', methods=['GET'])
@login_required
@require_perm('can_view_references')
def get_references():
    uid = session['user_id']
    if is_super_admin(): rows = q("SELECT r.*, u.full_name AS by_name FROM references_ r LEFT JOIN users u ON u.id=r.created_by ORDER BY r.id DESC")
    else: rows = q("SELECT r.*, u.full_name AS by_name FROM references_ r LEFT JOIN users u ON u.id=r.created_by WHERE r.created_by=%s ORDER BY r.id DESC", (uid,))
    return jsonify([serialize(r) for r in rows])

@app.route('/api/references', methods=['POST'])
@login_required
@require_perm('can_manage_references')
def add_reference():
    d = request.json or {}
    row = q_ret("INSERT INTO references_ (created_by,name,phone,student,university,amount,pay_date,pay_mode,utr,status,notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                (session['user_id'],d.get('name'),d.get('phone'),d.get('student'),d.get('university'),d.get('amount',0),d.get('pay_date') or None,d.get('pay_mode','Cash'),d.get('utr'),d.get('status','Paid'),d.get('notes')))
    return jsonify(serialize(row)), 201

@app.route('/api/references/<int:rid>', methods=['DELETE'])
@login_required
@require_perm('can_manage_references')
def delete_reference(rid):
    if not is_super_admin():
        if not q("SELECT id FROM references_ WHERE id=%s AND created_by=%s", (rid,session['user_id']), one=True): return jsonify({'error':'Access denied'}), 403
    q("DELETE FROM references_ WHERE id=%s", (rid,), commit=True); return jsonify({'success':True})

# UNIVERSITIES
@app.route('/api/universities', methods=['GET'])
@login_required
def get_universities():
    uid = session['user_id']; assigned = get_user_univs(uid)
    rows = q("SELECT u.id, u.name, u.state, u.color, u.is_active, COUNT(s.id)::int AS student_count FROM universities u LEFT JOIN students s ON s.university=u.name GROUP BY u.id,u.name,u.state,u.color,u.is_active ORDER BY u.name")
    if assigned: rows = [r for r in rows if r['name'] in assigned]
    return jsonify(rows)

@app.route('/api/universities', methods=['POST'])
@login_required
@require_perm('can_manage_universities')
def add_university():
    d = request.json or {}
    try: q_ret("INSERT INTO universities (name,state,color) VALUES (%s,%s,%s) RETURNING id", (d.get('name'),d.get('state'),d.get('color','#1A6CF6')))
    except psycopg2.errors.UniqueViolation: get_db().rollback(); return jsonify({'error':'Already exists'}), 409
    return jsonify({'success':True}), 201

@app.route('/api/universities/<int:uid_>', methods=['PUT'])
@login_required
@require_perm('can_manage_universities')
def update_university(uid_):
    d = request.json or {}
    q("UPDATE universities SET name=%s,state=%s,color=%s,is_active=%s WHERE id=%s",
      (d.get('name'),d.get('state'),d.get('color','#1A6CF6'),d.get('is_active',True),uid_), commit=True)
    return jsonify({'success':True})

@app.route('/api/universities/<int:uid_>', methods=['DELETE'])
@login_required
@require_perm('can_manage_universities')
def delete_university(uid_):
    q("DELETE FROM universities WHERE id=%s", (uid_,), commit=True); return jsonify({'success':True})

# ACADEMIC SESSIONS
@app.route('/api/sessions', methods=['GET'])
@login_required
def get_sessions():
    return jsonify([serialize(r) for r in q("SELECT * FROM academic_sessions ORDER BY id DESC")])

@app.route('/api/sessions', methods=['POST'])
@login_required
@super_admin_required
def add_session():
    d = request.json or {}
    row = q_ret("INSERT INTO academic_sessions (name,start_date,end_date,is_active,created_by) VALUES (%s,%s,%s,%s,%s) RETURNING *",
                (d.get('name'),d.get('start_date') or None,d.get('end_date') or None,False,session['user_id']))
    return jsonify(serialize(row)), 201

@app.route('/api/sessions/<int:sid>/activate', methods=['POST'])
@login_required
@super_admin_required
def activate_session(sid):
    q("UPDATE academic_sessions SET is_active=FALSE", commit=True)
    q("UPDATE academic_sessions SET is_active=TRUE WHERE id=%s", (sid,), commit=True)
    log_action('Activate','Session',sid)
    return jsonify({'success':True})

@app.route('/api/sessions/<int:sid>', methods=['DELETE'])
@login_required
@super_admin_required
def delete_session(sid):
    active = q("SELECT is_active FROM academic_sessions WHERE id=%s", (sid,), one=True)
    if active and active['is_active']: return jsonify({'error':'Cannot delete active session'}), 400
    q("DELETE FROM academic_sessions WHERE id=%s", (sid,), commit=True); return jsonify({'success':True})

# DOCUMENTS
@app.route('/api/documents', methods=['GET'])
@login_required
@require_perm('can_view_documents')
def get_documents():
    uid = session['user_id']; sid = request.args.get('student_id')
    fs, fp = student_filter(uid,'s')
    sql = "SELECT d.*, s.name AS student_name, u.full_name AS by_name FROM documents d LEFT JOIN students s ON s.id=d.student_id LEFT JOIN users u ON u.id=d.uploaded_by WHERE TRUE"
    params = []
    if not is_super_admin():
        sql += f" AND (d.student_id IS NULL OR d.student_id IN (SELECT id FROM students WHERE TRUE {fs}))"; params.extend(fp)
    if sid: sql += " AND d.student_id=%s"; params.append(int(sid))
    result = []
    for r in q(sql+' ORDER BY d.id DESC', params):
        sr = serialize(r)
        if r.get('file_path'): sr['file_url'] = f'/uploads/{r["file_path"]}'
        result.append(sr)
    return jsonify(result)

@app.route('/api/documents', methods=['POST'])
@login_required
@require_perm('can_upload_document')
def add_document():
    d = request.json or {}
    q_ret("INSERT INTO documents (student_id,student,doc_type,university,issue_date,status,delivered_to,uploaded_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
          (d.get('student_id'),d.get('student'),d.get('doc_type'),d.get('university'),d.get('issue_date') or None,d.get('status','Delivered'),d.get('delivered_to'),session['user_id']))
    return jsonify({'success':True}), 201

@app.route('/api/documents/<int:did>/upload', methods=['POST'])
@login_required
@require_perm('can_upload_document')
def upload_doc_file(did):
    if 'file' not in request.files: return jsonify({'error':'No file'}), 400
    file = request.files['file']
    if not allowed_file(file.filename): return jsonify({'error':'Invalid type'}), 400
    ext = file.filename.rsplit('.',1)[1].lower()
    filename = f"doc_{did}_{uuid.uuid4().hex[:8]}.{ext}"
    file.save(os.path.join(UPLOAD_FOLDER, filename))
    q("UPDATE documents SET file_path=%s,file_name=%s,uploaded_by=%s WHERE id=%s",
      (filename,file.filename,session['user_id'],did), commit=True)
    return jsonify({'success':True,'url':f'/uploads/{filename}'})

@app.route('/api/documents/<int:did>', methods=['DELETE'])
@login_required
@admin_required
def delete_document(did):
    doc = q("SELECT file_path FROM documents WHERE id=%s", (did,), one=True)
    if doc and doc.get('file_path'):
        try: os.remove(os.path.join(UPLOAD_FOLDER, doc['file_path']))
        except: pass
    q("DELETE FROM documents WHERE id=%s", (did,), commit=True); return jsonify({'success':True})

# NOTIFICATIONS
@app.route('/api/notifications', methods=['GET'])
@login_required
def get_notifications():
    uid = session['user_id']
    return jsonify([serialize(r) for r in q("SELECT * FROM notifications WHERE user_id=%s ORDER BY created_at DESC LIMIT 30", (uid,))])

@app.route('/api/notifications/read-all', methods=['POST'])
@login_required
def mark_all_read():
    q("UPDATE notifications SET is_read=TRUE WHERE user_id=%s", (session['user_id'],), commit=True); return jsonify({'success':True})

@app.route('/api/notifications/<int:nid>/read', methods=['POST'])
@login_required
def mark_read(nid):
    q("UPDATE notifications SET is_read=TRUE WHERE id=%s AND user_id=%s", (nid,session['user_id']), commit=True); return jsonify({'success':True})

# USERS
@app.route('/api/users', methods=['GET'])
@login_required
@admin_required
def get_users():
    rows = q("SELECT id,username,full_name,role,is_active,last_login,created_at FROM users ORDER BY id")
    result = []
    for r in rows:
        sr = serialize(r); sr['permissions'] = get_user_perms(r['id'])
        sr['assigned_universities'] = q("SELECT u.id,u.name FROM user_universities uu JOIN universities u ON u.id=uu.university_id WHERE uu.user_id=%s", (r['id'],))
        result.append(sr)
    return jsonify(result)

@app.route('/api/users', methods=['POST'])
@login_required
@admin_required
def add_user():
    d = request.json or {}
    try:
        new_user = q_ret("INSERT INTO users (username,password,full_name,role) VALUES (%s,%s,%s,%s) RETURNING id",
                         (d.get('username'), hash_pw(d.get('password','')), d.get('full_name'), d.get('role','Staff')))
    except psycopg2.errors.UniqueViolation:
        get_db().rollback(); return jsonify({'error':'Username already exists'}), 409
    uid = new_user['id']; perms = d.get('permissions',{})
    q_ret("INSERT INTO user_permissions (user_id,can_add_student,can_edit_student,can_delete_student,can_view_payments,can_add_payment,can_view_associates,can_manage_associates,can_view_references,can_manage_references,can_view_documents,can_upload_document,can_view_reports,can_manage_universities,can_view_all_students,can_manage_leads,can_view_audit_logs) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
          (uid,perms.get('can_add_student',True),perms.get('can_edit_student',True),perms.get('can_delete_student',False),perms.get('can_view_payments',True),perms.get('can_add_payment',True),perms.get('can_view_associates',False),perms.get('can_manage_associates',False),perms.get('can_view_references',False),perms.get('can_manage_references',False),perms.get('can_view_documents',True),perms.get('can_upload_document',True),perms.get('can_view_reports',False),perms.get('can_manage_universities',False),perms.get('can_view_all_students',False),perms.get('can_manage_leads',False),perms.get('can_view_audit_logs',False)))
    for univ_id in d.get('assigned_university_ids',[]):
        try: q_ret("INSERT INTO user_universities (user_id,university_id) VALUES (%s,%s) RETURNING id", (uid,univ_id))
        except Exception: get_db().rollback()
    notify_user(uid,'Welcome!',f"Sky Eduworld mein aapka swagat hai, {d.get('full_name')}!",'success')
    log_action('Add','User',uid,d.get('full_name'))
    return jsonify({'success':True,'id':uid}), 201

@app.route('/api/users/<int:uid>', methods=['PUT'])
@login_required
@admin_required
def update_user(uid):
    d = request.json or {}
    if d.get('full_name') or d.get('role'):
        q("UPDATE users SET full_name=%s,role=%s WHERE id=%s", (d.get('full_name'),d.get('role'),uid), commit=True)
    if d.get('new_password'):
        if len(d['new_password']) < 6: return jsonify({'error':'Min 6 chars'}), 400
        q("UPDATE users SET password=%s WHERE id=%s", (hash_pw(d['new_password']),uid), commit=True)
    if 'permissions' in d:
        perms = d['permissions']
        pv = (perms.get('can_add_student',True),perms.get('can_edit_student',True),perms.get('can_delete_student',False),perms.get('can_view_payments',True),perms.get('can_add_payment',True),perms.get('can_view_associates',False),perms.get('can_manage_associates',False),perms.get('can_view_references',False),perms.get('can_manage_references',False),perms.get('can_view_documents',True),perms.get('can_upload_document',True),perms.get('can_view_reports',False),perms.get('can_manage_universities',False),perms.get('can_view_all_students',False),perms.get('can_manage_leads',False),perms.get('can_view_audit_logs',False))
        if q("SELECT id FROM user_permissions WHERE user_id=%s", (uid,), one=True):
            q("UPDATE user_permissions SET can_add_student=%s,can_edit_student=%s,can_delete_student=%s,can_view_payments=%s,can_add_payment=%s,can_view_associates=%s,can_manage_associates=%s,can_view_references=%s,can_manage_references=%s,can_view_documents=%s,can_upload_document=%s,can_view_reports=%s,can_manage_universities=%s,can_view_all_students=%s,can_manage_leads=%s,can_view_audit_logs=%s WHERE user_id=%s", pv+(uid,), commit=True)
        else:
            q_ret("INSERT INTO user_permissions (user_id,can_add_student,can_edit_student,can_delete_student,can_view_payments,can_add_payment,can_view_associates,can_manage_associates,can_view_references,can_manage_references,can_view_documents,can_upload_document,can_view_reports,can_manage_universities,can_view_all_students,can_manage_leads,can_view_audit_logs) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id", (uid,)+pv)
    if 'assigned_university_ids' in d:
        q("DELETE FROM user_universities WHERE user_id=%s", (uid,), commit=True)
        for univ_id in d['assigned_university_ids']:
            try: q_ret("INSERT INTO user_universities (user_id,university_id) VALUES (%s,%s) RETURNING id", (uid,univ_id))
            except Exception: get_db().rollback()
    return jsonify({'success':True})

@app.route('/api/users/<int:uid>/toggle-active', methods=['POST'])
@login_required
@admin_required
def toggle_user_active(uid):
    if uid == session.get('user_id'): return jsonify({'error':'Cannot disable yourself'}), 400
    user = q("SELECT is_active FROM users WHERE id=%s", (uid,), one=True)
    if not user: return jsonify({'error':'Not found'}), 404
    new_state = not user['is_active']
    q("UPDATE users SET is_active=%s WHERE id=%s", (new_state,uid), commit=True)
    if not new_state: q("UPDATE users SET session_token=NULL WHERE id=%s", (uid,), commit=True)
    return jsonify({'success':True,'is_active':new_state})

@app.route('/api/users/<int:uid>/force-logout', methods=['POST'])
@login_required
@admin_required
def force_logout_user(uid):
    if uid == session.get('user_id'): return jsonify({'error':'Cannot force-logout yourself'}), 400
    q("UPDATE users SET session_token=NULL WHERE id=%s", (uid,), commit=True)
    return jsonify({'success':True})

@app.route('/api/users/<int:uid>', methods=['DELETE'])
@login_required
@admin_required
def delete_user(uid):
    if uid == session.get('user_id'): return jsonify({'error':'Cannot delete yourself'}), 400
    q("DELETE FROM users WHERE id=%s", (uid,), commit=True); return jsonify({'success':True})

@app.route('/api/change-password', methods=['POST'])
@login_required
def change_password():
    d = request.json or {}; user = q("SELECT * FROM users WHERE id=%s", (session['user_id'],), one=True)
    if user['password'] != hash_pw(d.get('old_password','')): return jsonify({'error':'Current password is wrong'}), 400
    new_pw = d.get('new_password','')
    if len(new_pw) < 6: return jsonify({'error':'Min 6 characters'}), 400
    q("UPDATE users SET password=%s WHERE id=%s", (hash_pw(new_pw),session['user_id']), commit=True)
    return jsonify({'success':True})

# AUDIT LOGS
@app.route('/api/audit-logs')
@login_required
@require_perm('can_view_audit_logs')
def get_audit_logs():
    uid_filter = request.args.get('user_id'); module = request.args.get('module','')
    limit = int(request.args.get('limit',100))
    sql = "SELECT al.*, u.full_name AS user_name FROM activity_logs al LEFT JOIN users u ON u.id=al.user_id WHERE TRUE"
    params = []
    if uid_filter: sql += " AND al.user_id=%s"; params.append(int(uid_filter))
    if module: sql += " AND al.module_name=%s"; params.append(module)
    return jsonify([serialize(r) for r in q(f"{sql} ORDER BY al.created_at DESC LIMIT {limit}", params)])

@app.route('/api/login-history')
@login_required
@admin_required
def get_login_history():
    uid_filter = request.args.get('user_id')
    sql = "SELECT lh.*, u.full_name AS user_name FROM login_history lh LEFT JOIN users u ON u.id=lh.user_id WHERE TRUE"
    params = []
    if uid_filter: sql += " AND lh.user_id=%s"; params.append(int(uid_filter))
    return jsonify([serialize(r) for r in q(sql+' ORDER BY lh.created_at DESC LIMIT 100', params)])

# BALANCE
@app.route('/api/balance/overview')
@login_required
def balance_overview():
    uid = session['user_id']; fs, fp = student_filter(uid)
    stats = q(f"SELECT COALESCE(SUM(total_fee),0) AS total_fee, COALESCE(SUM(paid),0) AS total_paid, COALESCE(SUM(total_fee-paid),0) AS outstanding, COALESCE(SUM(univ_fee),0) AS univ_fee_total, COUNT(*) AS student_count FROM students WHERE TRUE {fs}", fp, one=True)
    result = {k:float(v) if hasattr(v,'__float__') else v for k,v in stats.items()}
    daily = q(f"SELECT DATE(fp.created_at) AS day, COALESCE(SUM(fp.amount),0) AS amount FROM fee_payments fp JOIN students s ON s.id=fp.student_id WHERE fp.created_at >= CURRENT_DATE-7 {fs} GROUP BY DATE(fp.created_at) ORDER BY day", fp)
    result['daily_collections'] = [{'day':str(r['day']),'amount':float(r['amount'])} for r in daily]
    if is_super_admin():
        per_user = q("SELECT u.id, u.full_name, COALESCE(SUM(s.total_fee),0) AS total_fee, COALESCE(SUM(s.paid),0) AS total_paid, COALESCE(SUM(s.total_fee-s.paid),0) AS outstanding, COUNT(s.id) AS student_count FROM users u LEFT JOIN students s ON s.created_by=u.id WHERE u.role != 'Super Admin' GROUP BY u.id,u.full_name ORDER BY total_paid DESC")
        result['per_user'] = [{'id':r['id'],'full_name':r['full_name'],'total_fee':float(r['total_fee']),'total_paid':float(r['total_paid']),'outstanding':float(r['outstanding']),'student_count':r['student_count']} for r in per_user]
    return jsonify(result)

# REPORTS
def csv_response(rows, headers, filename):
    buf = io.StringIO(); w = csv.writer(buf); w.writerow(headers); w.writerows(rows)
    return Response('\ufeff'+buf.getvalue(), mimetype='text/csv; charset=utf-8-sig',
                    headers={'Content-Disposition':f'attachment; filename={filename}'})

@app.route('/api/reports/students')
@login_required
@require_perm('can_view_reports')
def report_students():
    uid = session['user_id']; fs, fp = student_filter(uid)
    data = q(f"SELECT * FROM students WHERE TRUE {fs} ORDER BY name", fp)
    rows = [[s['id'],s['name'],s['father'],s['mobile'],s['course'],s['university'],s['batch'],s['enroll_no'],s['adm_date'],float(s['total_fee']),float(s['paid']),float(s['total_fee'])-float(s['paid']),s['status']] for s in data]
    return csv_response(rows,['ID','Name','Father','Mobile','Course','University','Batch','Enrollment','Adm Date','Total Fee','Paid','Balance','Status'],f'Students_{datetime.now().strftime("%Y%m%d")}.csv')

@app.route('/api/reports/fees')
@login_required
@require_perm('can_view_reports')
def report_fees():
    uid = session['user_id']; fs, fp = student_filter(uid)
    data = q(f"SELECT * FROM students WHERE TRUE {fs} ORDER BY name", fp)
    rows = [[s['name'],s['university'],s['course'],float(s['total_fee']),float(s['paid']),float(s['total_fee'])-float(s['paid']),'Cleared' if float(s['total_fee'])==float(s['paid']) else 'Pending'] for s in data]
    return csv_response(rows,['Student','University','Course','Total Fee','Paid','Balance','Status'],f'Fees_{datetime.now().strftime("%Y%m%d")}.csv')

@app.route('/api/reports/outstanding')
@login_required
@require_perm('can_view_reports')
def report_outstanding():
    uid = session['user_id']; fs, fp = student_filter(uid)
    data = q(f"SELECT * FROM students WHERE paid < total_fee {fs} ORDER BY (total_fee-paid) DESC", fp)
    rows = [[s['name'],s['mobile'],s['university'],s['course'],float(s['total_fee']),float(s['paid']),float(s['total_fee'])-float(s['paid'])] for s in data]
    return csv_response(rows,['Student','Mobile','University','Course','Total Fee','Paid','Outstanding'],f'Outstanding_{datetime.now().strftime("%Y%m%d")}.csv')

@app.route('/api/reports/assoc-ref')
@login_required
@require_perm('can_view_reports')
def report_assoc_ref():
    uid = session['user_id']
    assocs = q("SELECT * FROM associates ORDER BY pay_date DESC") if is_super_admin() else q("SELECT * FROM associates WHERE created_by=%s ORDER BY pay_date DESC", (uid,))
    refs = q("SELECT * FROM references_ ORDER BY pay_date DESC") if is_super_admin() else q("SELECT * FROM references_ WHERE created_by=%s ORDER BY pay_date DESC", (uid,))
    rows = [['Associate',a['name'],a['phone'],a['student'],a['work_done'],float(a['amount']),a['pay_date'],a['pay_mode'],a['utr'],a['notes']] for a in assocs]
    rows += [['Reference',r['name'],r['phone'],r['student'],'Referral',float(r['amount']),r['pay_date'],r['pay_mode'],r['utr'],r['notes']] for r in refs]
    return csv_response(rows,['Type','Name','Phone','Student','Work','Amount','Date','Mode','UTR','Notes'],f'AssocRef_{datetime.now().strftime("%Y%m%d")}.csv')

@app.route('/api/reports/leads')
@login_required
def report_leads():
    uid = session['user_id']
    data = q("SELECT * FROM leads ORDER BY created_at DESC") if is_super_admin() else q("SELECT * FROM leads WHERE created_by=%s ORDER BY created_at DESC", (uid,))
    rows = [[r['name'],r['mobile'],r['course'],r['university'],r['source'],r['status'],str(r['created_at'])[:10]] for r in data]
    return csv_response(rows,['Name','Mobile','Course','University','Source','Status','Date'],f'Leads_{datetime.now().strftime("%Y%m%d")}.csv')

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT',5000))
    print(f"\n{'='*50}\n  Sky Eduworld Phase 2\n  URL: http://localhost:{port}\n  Login: admin / sky@2024\n{'='*50}\n")
    app.run(host='0.0.0.0', port=port, debug=os.environ.get('FLASK_ENV')=='development')
