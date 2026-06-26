"""
Sky Eduworld ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â Management System (PHASE 2 UPGRADE)
Backend: Flask + PostgreSQL
"""

import os, csv, io, hashlib, uuid, json
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
MAX_UPLOAD_BYTES = 512 * 1024


def parse_amount(v):
    try: return float(str(v or 0).replace(',','').strip() or 0)
    except Exception: return 0.0

def parse_date_value(v):
    v = str(v or '').strip()
    if not v: return None
    for fmt in ('%Y-%m-%d','%d-%m-%Y','%d/%m/%Y','%m/%d/%Y'):
        try: return datetime.strptime(v[:10], fmt).date().isoformat()
        except Exception: pass
    return v[:10]

def split_entries(text):
    if not text: return []
    return [x.strip() for x in str(text).replace('\n',';').split(';') if x.strip()]

def parse_payment_entries(text, default_type='Tuition Fee', default_mode='Cash'):
    out=[]
    for item in split_entries(text):
        parts=[p.strip() for p in item.split('|')]
        if len(parts) >= 6:
            out.append({'date':parse_date_value(parts[0]),'fee_type':parts[1] or default_type,'amount':parse_amount(parts[2]),'mode':parts[3] or default_mode,'ref':parts[4],'remarks':parts[5]})
        elif len(parts) >= 3:
            out.append({'date':parse_date_value(parts[0]),'fee_type':default_type,'amount':parse_amount(parts[1]),'mode':default_mode,'ref':'','remarks':parts[2]})
        elif len(parts) == 1:
            out.append({'date':None,'fee_type':default_type,'amount':parse_amount(parts[0]),'mode':default_mode,'ref':'','remarks':''})
    return [x for x in out if x['amount']>0]


def collect_numbered_payments(d, prefix, count=5, default_type='Tuition Fee', default_mode='Cash'):
    out=[]
    for n in range(1,count+1):
        amt=parse_amount(d.get(f'{prefix}{n}_amount'))
        if amt<=0: continue
        out.append({'date':parse_date_value(d.get(f'{prefix}{n}_date')),'fee_type':d.get(f'{prefix}{n}_type') or default_type,'amount':amt,'mode':d.get(f'{prefix}{n}_mode') or default_mode,'ref':d.get(f'{prefix}{n}_ref') or '','remarks':d.get(f'{prefix}{n}_remarks') or ''})
    return out

def collect_numbered_university_payments(d, prefix='univ_pay', count=5, default_type='Tuition', default_mode='Cash'):
    out=[]
    for n in range(1,count+1):
        payable=parse_amount(d.get(f'{prefix}{n}_payable'))
        paid=parse_amount(d.get(f'{prefix}{n}_paid'))
        if payable<=0 and paid<=0: continue
        out.append({'date':parse_date_value(d.get(f'{prefix}{n}_date')),'fee_type':d.get(f'{prefix}{n}_type') or default_type,'payable':payable,'paid':paid,'mode':d.get(f'{prefix}{n}_mode') or default_mode,'ref':d.get(f'{prefix}{n}_ref') or '','remarks':d.get(f'{prefix}{n}_remarks') or ''})
    return out

def parse_university_entries(text, default_fee_type='Tuition Fee', default_mode='Cash'):
    out=[]
    for item in split_entries(text):
        parts=[p.strip() for p in item.split('|')]
        if len(parts) >= 7:
            out.append({'date':parse_date_value(parts[0]),'fee_type':parts[1] or default_fee_type,'payable':parse_amount(parts[2]),'paid':parse_amount(parts[3]),'mode':parts[4] or default_mode,'ref':parts[5],'remarks':parts[6]})
        elif len(parts) >= 4:
            out.append({'date':parse_date_value(parts[0]),'fee_type':parts[1] or default_fee_type,'payable':parse_amount(parts[2]),'paid':parse_amount(parts[3]),'mode':default_mode,'ref':'','remarks':''})
    return [x for x in out if x['payable']>0 or x['paid']>0]

def norm_key(v):
    return str(v or '').strip().lower().replace('*','').replace('_',' ')

def first_val(d, *keys):
    if not d: return ''
    normalized = {norm_key(k): v for k, v in d.items()}
    for key in keys:
        if key in d and str(d.get(key) or '').strip() != '':
            return d.get(key)
        nk = norm_key(key)
        if nk in normalized and str(normalized[nk] or '').strip() != '':
            return normalized[nk]
    return ''

def clean_row(row):
    return {str(k or '').strip().replace('*','').strip(): ('' if v is None else str(v).strip()) for k, v in (row or {}).items()}

def make_student_code(sid):
    try:
        return f"STU{int(sid):06d}"
    except Exception:
        return ''

def assign_student_code(sid):
    code = make_student_code(sid)
    if not code: return None
    q("UPDATE students SET student_code=COALESCE(NULLIF(student_code,''),%s) WHERE id=%s", (code, sid), commit=True)
    return code


def norm_import_part(v):
    return ' '.join(str(v or '').strip().lower().split())

def student_import_key(d):
    parts = [norm_import_part(d.get('name')), norm_import_part(d.get('father')), norm_import_part(d.get('course')), norm_import_part(d.get('subject')), norm_import_part(d.get('university'))]
    if any(not p for p in parts):
        return None
    return hashlib.md5('|'.join(parts).encode('utf-8')).hexdigest()


def ensure_student_import_columns():
    try:
        q("ALTER TABLE students ADD COLUMN IF NOT EXISTS import_key TEXT", commit=True)
        q("ALTER TABLE students ADD COLUMN IF NOT EXISTS student_code TEXT", commit=True)
        q("ALTER TABLE students ADD COLUMN IF NOT EXISTS external_id TEXT", commit=True)
        q("ALTER TABLE fee_installments ADD COLUMN IF NOT EXISTS fee_type TEXT", commit=True)
        q("CREATE INDEX IF NOT EXISTS idx_students_import_key ON students(import_key)", commit=True)
    except Exception:
        get_db().rollback()

def find_student_for_import(row, allow_name_match=True):
    sid = first_val(row, 'student_id', 'Student ID', 'ID', 'Access ID', 'Old Student ID', 'Import Student ID')
    if sid:
        sid_text = str(sid).strip()
        sid_int = sid_text[:-2] if sid_text.endswith('.0') else sid_text
        st = q("SELECT * FROM students WHERE external_id IN (%s,%s) ORDER BY id DESC LIMIT 1", (sid_text, sid_int), one=True)
        if st: return st
    student_code = first_val(row, 'student_code', 'Student Code', 'Auto ID', 'Code')
    if student_code:
        st = q("SELECT * FROM students WHERE UPPER(student_code)=UPPER(%s) ORDER BY id DESC LIMIT 1", (student_code,), one=True)
        if st: return st
    mobile = first_val(row, 'mobile', 'Contact No', 'Contact', 'Phone')
    name = first_val(row, 'name', 'student_name', 'Student Name', 'Student')
    if mobile:
        st = q("SELECT * FROM students WHERE regexp_replace(COALESCE(mobile,''),'[^0-9]','','g')=regexp_replace(%s,'[^0-9]','','g') ORDER BY id DESC LIMIT 1", (mobile,), one=True)
        if st: return st
    if allow_name_match and name:
        st = q("SELECT * FROM students WHERE LOWER(trim(name))=LOWER(trim(%s)) ORDER BY id DESC LIMIT 1", (name,), one=True)
        if st: return st
        normalized_name = ' '.join(str(name or '').split())
        st = q("SELECT * FROM students WHERE regexp_replace(LOWER(COALESCE(name,'')),'\\s+',' ','g')=LOWER(%s) ORDER BY id DESC LIMIT 1", (normalized_name,), one=True)
        if st: return st
    return None

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

def current_tenant_id():
    if session.get('tenant_id'): return session.get('tenant_id')
    uid = session.get('user_id')
    if not uid: return None
    row = q("SELECT tenant_id FROM users WHERE id=%s", (uid,), one=True)
    return row.get('tenant_id') if row else None

def tenant_filter(alias='', include_super=False):
    if is_super_admin() and not include_super:
        return '', []
    tid = current_tenant_id()
    if not tid:
        return '', []
    p = f"{alias}." if alias else ""
    return f" AND {p}tenant_id=%s", [tid]

def ensure_tenant_access(tid):
    return is_super_admin() or (tid and int(tid) == int(current_tenant_id() or 0))

def get_user_perms(user_id):
    user_row = q("SELECT role FROM users WHERE id=%s", (user_id,), one=True)
    if user_row and user_row['role'] == 'Super Admin':
        return {p:True for p in [
            'can_add_student','can_edit_student','can_delete_student','can_save_partial_student',
            'can_view_payments','can_add_payment','can_view_fee_types','can_manage_fee_types',
            'can_view_associates','can_manage_associates',
            'can_view_references','can_manage_references',
            'can_view_documents','can_upload_document','can_issue_document','can_delete_document','can_manage_masters',
            'can_view_student_report','can_view_fee_report',
            'can_view_outstanding_report','can_view_assocref_report','can_view_leads_report',
            'can_manage_universities','can_view_all_students','can_view_accounts','can_manage_accounts','can_view_profit_report',
            'can_manage_leads','can_view_audit_logs','can_manage_users','can_download_backup','can_view_reports']}
    perms = q("SELECT * FROM user_permissions WHERE user_id=%s", (user_id,), one=True)
    if not perms:
        return {
            'can_add_student':True,'can_edit_student':True,'can_delete_student':False,'can_save_partial_student':False,
            'can_view_payments':True,'can_add_payment':True,'can_view_fee_types':True,'can_manage_fee_types':False,
            'can_view_associates':False,'can_manage_associates':False,
            'can_view_references':False,'can_manage_references':False,
            'can_view_documents':True,'can_upload_document':True,'can_issue_document':False,'can_delete_document':False,'can_manage_masters':False,
            'can_view_student_report':False,'can_view_fee_report':False,
            'can_view_outstanding_report':False,'can_view_assocref_report':False,'can_view_leads_report':False,
            'can_manage_universities':False,'can_view_all_students':False,
            'can_manage_leads':False,'can_view_audit_logs':False,'can_manage_users':False,'can_download_backup':False,'can_view_accounts':False,'can_manage_accounts':False,'can_view_profit_report':False,'can_view_reports':False}
    out = dict(perms)
    if out.get('can_manage_references'):
        out['can_view_references'] = True
    if out.get('can_manage_associates'):
        out['can_view_associates'] = True
    if out.get('can_manage_accounts'):
        out['can_view_accounts'] = True
    if out.get('can_manage_fee_types'):
        out['can_view_fee_types'] = True
    out['can_view_reports'] = bool(out.get('can_view_reports') or out.get('can_view_student_report') or out.get('can_view_fee_report') or out.get('can_view_outstanding_report') or out.get('can_view_assocref_report') or out.get('can_view_leads_report') or out.get('can_view_profit_report'))
    return out

def get_user_univs(user_id):
    if is_super_admin(): return []
    rows = q("SELECT u.name FROM user_universities uu JOIN universities u ON u.id=uu.university_id WHERE uu.user_id=%s AND u.tenant_id=%s", (user_id,current_tenant_id()))
    return [r['name'] for r in rows]

def student_filter(user_id, alias=''):
    p = f"{alias}." if alias else ""
    perms = get_user_perms(user_id)
    clauses, params = [], []
    if not perms.get('can_view_all_students') and not is_super_admin():
        clauses.append(f"{p}created_by = %s"); params.append(user_id)
    tf, tp = tenant_filter(alias)
    if tf:
        clauses.append(tf.replace(' AND ','',1)); params.extend(tp)
    univs = get_user_univs(user_id)
    if univs:
        ph = ','.join(['%s']*len(univs)); clauses.append(f"{p}university IN ({ph})"); params.extend(univs)
    sql = (' AND ' + ' AND '.join(clauses)) if clauses else ''
    return sql, params

def get_active_session():
    tf,tp = tenant_filter('', include_super=True)
    return q("SELECT * FROM academic_sessions WHERE is_active=TRUE"+tf+" ORDER BY id DESC LIMIT 1", tp, one=True)

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
    CREATE TABLE IF NOT EXISTS tenants (id SERIAL PRIMARY KEY, name TEXT UNIQUE NOT NULL, status TEXT DEFAULT 'Active', subscription_start DATE DEFAULT CURRENT_DATE, subscription_end DATE, notes TEXT, created_at TIMESTAMP DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS users (id SERIAL PRIMARY KEY, tenant_id INTEGER REFERENCES tenants(id), username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, full_name TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'Staff', is_active BOOLEAN DEFAULT TRUE, session_token TEXT, failed_logins INTEGER DEFAULT 0, last_login TIMESTAMP, created_at TIMESTAMP DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS user_permissions (id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        can_add_student BOOLEAN DEFAULT TRUE, can_edit_student BOOLEAN DEFAULT TRUE, can_delete_student BOOLEAN DEFAULT FALSE, can_save_partial_student BOOLEAN DEFAULT FALSE,
        can_view_payments BOOLEAN DEFAULT TRUE, can_add_payment BOOLEAN DEFAULT TRUE, can_view_fee_types BOOLEAN DEFAULT TRUE, can_manage_fee_types BOOLEAN DEFAULT FALSE,
        can_view_associates BOOLEAN DEFAULT FALSE, can_manage_associates BOOLEAN DEFAULT FALSE,
        can_view_references BOOLEAN DEFAULT FALSE, can_manage_references BOOLEAN DEFAULT FALSE,
        can_view_documents BOOLEAN DEFAULT TRUE, can_upload_document BOOLEAN DEFAULT TRUE, can_issue_document BOOLEAN DEFAULT FALSE, can_delete_document BOOLEAN DEFAULT FALSE, can_manage_masters BOOLEAN DEFAULT FALSE,
        can_view_student_report BOOLEAN DEFAULT FALSE, can_view_fee_report BOOLEAN DEFAULT FALSE,
        can_view_outstanding_report BOOLEAN DEFAULT FALSE, can_view_assocref_report BOOLEAN DEFAULT FALSE, can_view_leads_report BOOLEAN DEFAULT FALSE,
        can_manage_universities BOOLEAN DEFAULT FALSE, can_view_all_students BOOLEAN DEFAULT FALSE,
        can_manage_leads BOOLEAN DEFAULT FALSE, can_view_audit_logs BOOLEAN DEFAULT FALSE, can_manage_users BOOLEAN DEFAULT FALSE, can_download_backup BOOLEAN DEFAULT FALSE, can_view_accounts BOOLEAN DEFAULT FALSE, can_manage_accounts BOOLEAN DEFAULT FALSE, can_view_profit_report BOOLEAN DEFAULT FALSE,
        UNIQUE(user_id));
    CREATE TABLE IF NOT EXISTS universities (id SERIAL PRIMARY KEY, tenant_id INTEGER, name TEXT NOT NULL, state TEXT, color TEXT DEFAULT '#1A6CF6', is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS guides (id SERIAL PRIMARY KEY, university_id INTEGER REFERENCES universities(id) ON DELETE CASCADE, name TEXT NOT NULL, designation TEXT NOT NULL, department TEXT, subject TEXT, mobile TEXT, email TEXT, assigned_students INTEGER DEFAULT 0, file_path TEXT, file_name TEXT, created_by INTEGER REFERENCES users(id), created_at TIMESTAMP DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS user_universities (id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, university_id INTEGER NOT NULL REFERENCES universities(id) ON DELETE CASCADE, UNIQUE(user_id,university_id));
    CREATE TABLE IF NOT EXISTS academic_sessions (id SERIAL PRIMARY KEY, tenant_id INTEGER, name TEXT NOT NULL, start_date DATE, end_date DATE, is_active BOOLEAN DEFAULT FALSE, created_by INTEGER REFERENCES users(id), created_at TIMESTAMP DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS fee_types (id SERIAL PRIMARY KEY, tenant_id INTEGER, name TEXT NOT NULL, category TEXT DEFAULT 'Student Fee', is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS courses (id SERIAL PRIMARY KEY, tenant_id INTEGER, name TEXT NOT NULL, is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS subjects (id SERIAL PRIMARY KEY, tenant_id INTEGER, name TEXT NOT NULL, course_name TEXT, is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS document_types (id SERIAL PRIMARY KEY, tenant_id INTEGER, name TEXT NOT NULL, category TEXT DEFAULT 'Student', is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS student_statuses (id SERIAL PRIMARY KEY, tenant_id INTEGER, name TEXT NOT NULL, category TEXT DEFAULT 'Student', is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS students (id SERIAL PRIMARY KEY, student_code TEXT UNIQUE, external_id TEXT, import_key TEXT, created_by INTEGER REFERENCES users(id), session_id INTEGER REFERENCES academic_sessions(id), name TEXT NOT NULL, father TEXT, mother TEXT, dob DATE, gender TEXT, mobile TEXT, email TEXT, aadhar TEXT, address TEXT, course TEXT, subject TEXT, university TEXT, batch TEXT, enroll_no TEXT, roll_no TEXT, adm_date DATE, remarks TEXT, total_fee NUMERIC(12,2) DEFAULT 0, paid NUMERIC(12,2) DEFAULT 0, univ_fee NUMERIC(12,2) DEFAULT 0, pay_mode TEXT, utr TEXT, doc_notes TEXT, status TEXT DEFAULT 'Active', photo_path TEXT, created_at TIMESTAMP DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS fee_installments (id SERIAL PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE, created_by INTEGER REFERENCES users(id), amount NUMERIC(12,2) NOT NULL, fee_type TEXT, due_date DATE, paid_date DATE, status TEXT DEFAULT 'Pending', remarks TEXT, created_at TIMESTAMP DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS fee_payments (id SERIAL PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE, recorded_by INTEGER REFERENCES users(id), installment_id INTEGER REFERENCES fee_installments(id), amount NUMERIC(12,2) NOT NULL, fee_type TEXT, pay_mode TEXT, ref_no TEXT, pay_date DATE, remarks TEXT, created_at TIMESTAMP DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS associates (id SERIAL PRIMARY KEY, created_by INTEGER REFERENCES users(id), parent_id INTEGER REFERENCES associates(id) ON DELETE CASCADE, name TEXT NOT NULL, phone TEXT, student TEXT, work_done TEXT, amount NUMERIC(12,2) DEFAULT 0, paid_amount NUMERIC(12,2) DEFAULT 0, pay_date DATE, pay_mode TEXT, utr TEXT, status TEXT DEFAULT 'Paid', notes TEXT, created_at TIMESTAMP DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS references_ (id SERIAL PRIMARY KEY, created_by INTEGER REFERENCES users(id), parent_id INTEGER REFERENCES references_(id) ON DELETE CASCADE, name TEXT NOT NULL, phone TEXT, student TEXT, university TEXT, amount NUMERIC(12,2) DEFAULT 0, paid_amount NUMERIC(12,2) DEFAULT 0, pay_date DATE, pay_mode TEXT, utr TEXT, status TEXT DEFAULT 'Paid', notes TEXT, created_at TIMESTAMP DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS documents (id SERIAL PRIMARY KEY, student_id INTEGER REFERENCES students(id) ON DELETE SET NULL, student TEXT NOT NULL, doc_type TEXT NOT NULL, university TEXT, issue_date DATE, status TEXT DEFAULT 'Delivered', delivered_to TEXT, file_path TEXT, file_name TEXT, uploaded_by INTEGER REFERENCES users(id), created_at TIMESTAMP DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS student_photos (id SERIAL PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE, file_path TEXT NOT NULL, file_name TEXT, uploaded_by INTEGER REFERENCES users(id), uploaded_at TIMESTAMP DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS leads (id SERIAL PRIMARY KEY, created_by INTEGER REFERENCES users(id), name TEXT NOT NULL, mobile TEXT, email TEXT, course TEXT, university TEXT, source TEXT DEFAULT 'Walk-in', status TEXT DEFAULT 'New', remarks TEXT, follow_up_date DATE, converted_to INTEGER REFERENCES students(id), created_at TIMESTAMP DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS follow_ups (id SERIAL PRIMARY KEY, created_by INTEGER REFERENCES users(id), student_id INTEGER REFERENCES students(id) ON DELETE CASCADE, lead_id INTEGER REFERENCES leads(id) ON DELETE CASCADE, note TEXT NOT NULL, follow_type TEXT DEFAULT 'Call', next_date DATE, created_at TIMESTAMP DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS activity_logs (id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id), action_type TEXT NOT NULL, module_name TEXT, record_id INTEGER, detail TEXT, ip_address TEXT, created_at TIMESTAMP DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS login_history (id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id), username TEXT, status TEXT DEFAULT 'Success', ip_address TEXT, created_at TIMESTAMP DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS notifications (id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id), title TEXT NOT NULL, message TEXT, type TEXT DEFAULT 'info', is_read BOOLEAN DEFAULT FALSE, link TEXT, created_at TIMESTAMP DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS university_payables (id SERIAL PRIMARY KEY, student_id INTEGER REFERENCES students(id) ON DELETE SET NULL, created_by INTEGER REFERENCES users(id), university TEXT, student TEXT, amount NUMERIC(12,2) DEFAULT 0, paid_amount NUMERIC(12,2) DEFAULT 0, fee_type TEXT DEFAULT 'Tuition', due_date DATE, paid_date DATE, pay_mode TEXT, ref_no TEXT, status TEXT DEFAULT 'Pending', remarks TEXT, created_at TIMESTAMP DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS expenses (id SERIAL PRIMARY KEY, created_by INTEGER REFERENCES users(id), expense_date DATE DEFAULT CURRENT_DATE, category TEXT DEFAULT 'Office', amount NUMERIC(12,2) NOT NULL, pay_mode TEXT, paid_to TEXT, student TEXT, university TEXT, associate TEXT, reference_name TEXT, remarks TEXT, created_at TIMESTAMP DEFAULT NOW());
    """)
    cur.execute("INSERT INTO tenants (name,status,notes) VALUES (%s,%s,%s) ON CONFLICT (name) DO NOTHING", ('Sky Eduworld','Active','Default tenant for existing data'))
    cur.execute("SELECT id FROM tenants WHERE name='Sky Eduworld'")
    default_tenant = cur.fetchone()['id']
    cur.execute("SELECT id FROM users WHERE username='admin'")
    if not cur.fetchone():
        cur.execute("INSERT INTO users (tenant_id,username,password,full_name,role) VALUES (%s,%s,%s,%s,%s)",
                    (default_tenant, 'admin', hash_pw('sky@2024'), 'Admin', 'Super Admin'))
    else:
        cur.execute("UPDATE users SET tenant_id=COALESCE(tenant_id,%s), is_active=TRUE, role='Super Admin' WHERE username='admin'", (default_tenant,))
    cur.execute("SELECT id FROM academic_sessions LIMIT 1")
    if not cur.fetchone():
        cur.execute("INSERT INTO academic_sessions (tenant_id,name,start_date,end_date,is_active) VALUES (%s,%s,%s,%s,%s)",
                    (default_tenant,'2025-26','2025-07-01','2026-06-30',True))
    for c in ['B.Ed','M.Ed','BPT','MBA','BBA','B.Com','M.Com','BA','MA','BCA','MCA','B.Sc','M.Sc','D.El.Ed','B.P.Ed']:
        cur.execute("INSERT INTO courses (tenant_id,name) VALUES (%s,%s) ON CONFLICT DO NOTHING", (default_tenant,c))
    for sub in ['Hindi','English','Education','Political Science','History','Sociology','Commerce','Computer Application','Management','Science','Mathematics']:
        cur.execute("INSERT INTO subjects (tenant_id,name) VALUES (%s,%s) ON CONFLICT DO NOTHING", (default_tenant,sub))
    for ft in ['Tuition Fee','Examination Fee','Registration Fee','Degree','Migration','Notification','Other Fee']:
        cur.execute("INSERT INTO fee_types (tenant_id,name) VALUES (%s,%s) ON CONFLICT DO NOTHING", (default_tenant,ft))
    for dt in ['Aadhar Card','10th Marksheet','12th Marksheet','Graduation Certificate','Passport Photos','Transfer Certificate','Admission Letter','Admission Confirmation Letter','Course Work Letter','Course Work Marksheet','Examination Hall Ticket','Degree Certificate','Migration Certificate','Migration / PG Document','Provisional Certificate','PG Document','PG Marksheet','PG Degree','Affidavit','All Documents']:
        cur.execute("INSERT INTO document_types (tenant_id,name) VALUES (%s,%s) ON CONFLICT DO NOTHING", (default_tenant,dt))
    for st in ['Active','In Process','Pending','Completed','Dropped','Cancelled','Refund','Draft']:
        cur.execute("INSERT INTO student_statuses (tenant_id,name) VALUES (%s,%s) ON CONFLICT DO NOTHING", (default_tenant,st))
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
        cur.execute("INSERT INTO universities (tenant_id,name,state,color) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING", (default_tenant,)+u)
    for m in [
              "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE",
              "ALTER TABLE users ADD COLUMN IF NOT EXISTS tenant_id INTEGER",
              "UPDATE users SET tenant_id=(SELECT id FROM tenants WHERE name='Sky Eduworld' LIMIT 1) WHERE tenant_id IS NULL",
              "ALTER TABLE users ADD COLUMN IF NOT EXISTS session_token TEXT",
              "ALTER TABLE users ADD COLUMN IF NOT EXISTS failed_logins INTEGER DEFAULT 0",
              "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login TIMESTAMP",
              "ALTER TABLE students ADD COLUMN IF NOT EXISTS student_code TEXT",
              "ALTER TABLE students ADD COLUMN IF NOT EXISTS external_id TEXT",
              "ALTER TABLE students ADD COLUMN IF NOT EXISTS import_key TEXT",
        "ALTER TABLE fee_installments ADD COLUMN IF NOT EXISTS fee_type TEXT",
              "UPDATE students SET import_key=md5(lower(trim(coalesce(name,'')))||'|'||lower(trim(coalesce(father,'')))||'|'||lower(trim(coalesce(course,'')))||'|'||lower(trim(coalesce(subject,'')))||'|'||lower(trim(coalesce(university,'')))) WHERE (import_key IS NULL OR import_key='') AND trim(coalesce(name,''))<>'' AND trim(coalesce(father,''))<>'' AND trim(coalesce(course,''))<>'' AND trim(coalesce(subject,''))<>'' AND trim(coalesce(university,''))<>''",
              "CREATE INDEX IF NOT EXISTS idx_students_import_key ON students(import_key)",
              "UPDATE students SET student_code='STU' || LPAD(id::text,6,'0') WHERE student_code IS NULL OR student_code=''",
              "CREATE UNIQUE INDEX IF NOT EXISTS idx_students_student_code ON students(student_code)",
              "ALTER TABLE students ADD COLUMN IF NOT EXISTS created_by INTEGER",
              "ALTER TABLE students ADD COLUMN IF NOT EXISTS photo_path TEXT",
              "ALTER TABLE students ADD COLUMN IF NOT EXISTS session_id INTEGER",
              "ALTER TABLE students ADD COLUMN IF NOT EXISTS subject TEXT",
              "ALTER TABLE universities ADD COLUMN IF NOT EXISTS tenant_id INTEGER",
              "ALTER TABLE academic_sessions ADD COLUMN IF NOT EXISTS tenant_id INTEGER",
              "ALTER TABLE fee_types ADD COLUMN IF NOT EXISTS tenant_id INTEGER",
              "ALTER TABLE courses ADD COLUMN IF NOT EXISTS tenant_id INTEGER",
              "ALTER TABLE subjects ADD COLUMN IF NOT EXISTS tenant_id INTEGER",
              "ALTER TABLE document_types ADD COLUMN IF NOT EXISTS tenant_id INTEGER",
              "ALTER TABLE student_statuses ADD COLUMN IF NOT EXISTS tenant_id INTEGER",
              "ALTER TABLE students ADD COLUMN IF NOT EXISTS tenant_id INTEGER",
              "ALTER TABLE fee_installments ADD COLUMN IF NOT EXISTS tenant_id INTEGER",
              "ALTER TABLE fee_payments ADD COLUMN IF NOT EXISTS tenant_id INTEGER",
              "ALTER TABLE associates ADD COLUMN IF NOT EXISTS tenant_id INTEGER",
              "ALTER TABLE references_ ADD COLUMN IF NOT EXISTS tenant_id INTEGER",
              "ALTER TABLE documents ADD COLUMN IF NOT EXISTS tenant_id INTEGER",
              "ALTER TABLE student_photos ADD COLUMN IF NOT EXISTS tenant_id INTEGER",
              "ALTER TABLE leads ADD COLUMN IF NOT EXISTS tenant_id INTEGER",
              "ALTER TABLE follow_ups ADD COLUMN IF NOT EXISTS tenant_id INTEGER",
              "ALTER TABLE university_payables ADD COLUMN IF NOT EXISTS tenant_id INTEGER",
              "ALTER TABLE expenses ADD COLUMN IF NOT EXISTS tenant_id INTEGER",
              "ALTER TABLE guides ADD COLUMN IF NOT EXISTS tenant_id INTEGER",
              "UPDATE universities SET tenant_id=(SELECT id FROM tenants WHERE name='Sky Eduworld' LIMIT 1) WHERE tenant_id IS NULL",
              "UPDATE academic_sessions SET tenant_id=(SELECT id FROM tenants WHERE name='Sky Eduworld' LIMIT 1) WHERE tenant_id IS NULL",
              "UPDATE fee_types SET tenant_id=(SELECT id FROM tenants WHERE name='Sky Eduworld' LIMIT 1) WHERE tenant_id IS NULL",
              "UPDATE courses SET tenant_id=(SELECT id FROM tenants WHERE name='Sky Eduworld' LIMIT 1) WHERE tenant_id IS NULL",
              "UPDATE subjects SET tenant_id=(SELECT id FROM tenants WHERE name='Sky Eduworld' LIMIT 1) WHERE tenant_id IS NULL",
              "UPDATE document_types SET tenant_id=(SELECT id FROM tenants WHERE name='Sky Eduworld' LIMIT 1) WHERE tenant_id IS NULL",
              "UPDATE student_statuses SET tenant_id=(SELECT id FROM tenants WHERE name='Sky Eduworld' LIMIT 1) WHERE tenant_id IS NULL",
              "UPDATE students s SET tenant_id=COALESCE(u.tenant_id,(SELECT id FROM tenants WHERE name='Sky Eduworld' LIMIT 1)) FROM users u WHERE s.created_by=u.id AND s.tenant_id IS NULL",
              "UPDATE students SET tenant_id=(SELECT id FROM tenants WHERE name='Sky Eduworld' LIMIT 1) WHERE tenant_id IS NULL",
              "UPDATE fee_installments fi SET tenant_id=s.tenant_id FROM students s WHERE fi.student_id=s.id AND fi.tenant_id IS NULL",
              "UPDATE fee_payments fp SET tenant_id=s.tenant_id FROM students s WHERE fp.student_id=s.id AND fp.tenant_id IS NULL",
              "UPDATE university_payables up SET tenant_id=s.tenant_id FROM students s WHERE up.student_id=s.id AND up.tenant_id IS NULL",
              "UPDATE documents d SET tenant_id=s.tenant_id FROM students s WHERE d.student_id=s.id AND d.tenant_id IS NULL",
              "UPDATE student_photos sp SET tenant_id=s.tenant_id FROM students s WHERE sp.student_id=s.id AND sp.tenant_id IS NULL",
              "UPDATE leads l SET tenant_id=COALESCE(u.tenant_id,(SELECT id FROM tenants WHERE name='Sky Eduworld' LIMIT 1)) FROM users u WHERE l.created_by=u.id AND l.tenant_id IS NULL",
              "UPDATE follow_ups f SET tenant_id=COALESCE(u.tenant_id,(SELECT id FROM tenants WHERE name='Sky Eduworld' LIMIT 1)) FROM users u WHERE f.created_by=u.id AND f.tenant_id IS NULL",
              "UPDATE associates a SET tenant_id=COALESCE(u.tenant_id,(SELECT id FROM tenants WHERE name='Sky Eduworld' LIMIT 1)) FROM users u WHERE a.created_by=u.id AND a.tenant_id IS NULL",
              "UPDATE references_ r SET tenant_id=COALESCE(u.tenant_id,(SELECT id FROM tenants WHERE name='Sky Eduworld' LIMIT 1)) FROM users u WHERE r.created_by=u.id AND r.tenant_id IS NULL",
              "UPDATE expenses e SET tenant_id=COALESCE(u.tenant_id,(SELECT id FROM tenants WHERE name='Sky Eduworld' LIMIT 1)) FROM users u WHERE e.created_by=u.id AND e.tenant_id IS NULL",
              "UPDATE guides g SET tenant_id=u.tenant_id FROM universities u WHERE g.university_id=u.id AND g.tenant_id IS NULL",
              "ALTER TABLE universities DROP CONSTRAINT IF EXISTS universities_name_key",
              "ALTER TABLE courses DROP CONSTRAINT IF EXISTS courses_name_key",
              "ALTER TABLE subjects DROP CONSTRAINT IF EXISTS subjects_name_key",
              "ALTER TABLE fee_types DROP CONSTRAINT IF EXISTS fee_types_name_key",
              "ALTER TABLE document_types DROP CONSTRAINT IF EXISTS document_types_name_key",
              "ALTER TABLE student_statuses DROP CONSTRAINT IF EXISTS student_statuses_name_key",
              "CREATE UNIQUE INDEX IF NOT EXISTS idx_universities_tenant_name ON universities(tenant_id, lower(name))",
              "CREATE UNIQUE INDEX IF NOT EXISTS idx_courses_tenant_name ON courses(tenant_id, lower(name))",
              "CREATE UNIQUE INDEX IF NOT EXISTS idx_subjects_tenant_name ON subjects(tenant_id, lower(name))",
              "CREATE UNIQUE INDEX IF NOT EXISTS idx_fee_types_tenant_name ON fee_types(tenant_id, lower(name))",
              "CREATE UNIQUE INDEX IF NOT EXISTS idx_document_types_tenant_name ON document_types(tenant_id, lower(name))",
              "CREATE UNIQUE INDEX IF NOT EXISTS idx_student_statuses_tenant_name ON student_statuses(tenant_id, lower(name))",
              "ALTER TABLE guides ADD COLUMN IF NOT EXISTS assigned_students INTEGER DEFAULT 0",
              "ALTER TABLE guides ADD COLUMN IF NOT EXISTS file_path TEXT",
              "ALTER TABLE guides ADD COLUMN IF NOT EXISTS file_name TEXT",
              "CREATE TABLE IF NOT EXISTS guide_students (id SERIAL PRIMARY KEY, guide_id INTEGER NOT NULL REFERENCES guides(id) ON DELETE CASCADE, student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE, created_at TIMESTAMP DEFAULT NOW(), UNIQUE(guide_id,student_id))",
              "ALTER TABLE user_permissions ADD COLUMN IF NOT EXISTS can_save_partial_student BOOLEAN DEFAULT FALSE",
              "ALTER TABLE university_payables ADD COLUMN IF NOT EXISTS fee_type TEXT DEFAULT 'Tuition'",
              "ALTER TABLE fee_payments ADD COLUMN IF NOT EXISTS recorded_by INTEGER",
              "ALTER TABLE user_permissions ADD COLUMN IF NOT EXISTS can_view_fee_types BOOLEAN DEFAULT TRUE",
              "ALTER TABLE user_permissions ADD COLUMN IF NOT EXISTS can_manage_fee_types BOOLEAN DEFAULT FALSE",
              "ALTER TABLE user_permissions ADD COLUMN IF NOT EXISTS can_download_backup BOOLEAN DEFAULT FALSE",
              "ALTER TABLE fee_payments ADD COLUMN IF NOT EXISTS installment_id INTEGER",
              "ALTER TABLE fee_payments ADD COLUMN IF NOT EXISTS account_bucket TEXT DEFAULT 'student_receivable'",
              "ALTER TABLE associates ADD COLUMN IF NOT EXISTS account_bucket TEXT DEFAULT 'associate_expense'",
              "ALTER TABLE references_ ADD COLUMN IF NOT EXISTS account_bucket TEXT DEFAULT 'reference_expense'",
              "ALTER TABLE associates ADD COLUMN IF NOT EXISTS created_by INTEGER",
              "ALTER TABLE associates ADD COLUMN IF NOT EXISTS parent_id INTEGER",
              "ALTER TABLE associates ADD COLUMN IF NOT EXISTS paid_amount NUMERIC(12,2) DEFAULT 0",
              "ALTER TABLE references_ ADD COLUMN IF NOT EXISTS created_by INTEGER",
              "ALTER TABLE references_ ADD COLUMN IF NOT EXISTS parent_id INTEGER",
              "ALTER TABLE references_ ADD COLUMN IF NOT EXISTS paid_amount NUMERIC(12,2) DEFAULT 0",
              "ALTER TABLE user_permissions ADD COLUMN IF NOT EXISTS can_manage_leads BOOLEAN DEFAULT FALSE",
              "ALTER TABLE user_permissions ADD COLUMN IF NOT EXISTS can_view_audit_logs BOOLEAN DEFAULT FALSE",
              "ALTER TABLE user_permissions ADD COLUMN IF NOT EXISTS can_manage_users BOOLEAN DEFAULT FALSE",
              "ALTER TABLE user_permissions ADD COLUMN IF NOT EXISTS can_issue_document BOOLEAN DEFAULT FALSE",
              "ALTER TABLE user_permissions ADD COLUMN IF NOT EXISTS can_view_student_report BOOLEAN DEFAULT FALSE",
              "ALTER TABLE user_permissions ADD COLUMN IF NOT EXISTS can_view_fee_report BOOLEAN DEFAULT FALSE",
              "ALTER TABLE user_permissions ADD COLUMN IF NOT EXISTS can_view_outstanding_report BOOLEAN DEFAULT FALSE",
              "ALTER TABLE user_permissions ADD COLUMN IF NOT EXISTS can_view_assocref_report BOOLEAN DEFAULT FALSE",
              "ALTER TABLE user_permissions ADD COLUMN IF NOT EXISTS can_view_leads_report BOOLEAN DEFAULT FALSE",
              "ALTER TABLE user_permissions ADD COLUMN IF NOT EXISTS can_delete_document BOOLEAN DEFAULT FALSE",
              "ALTER TABLE user_permissions ADD COLUMN IF NOT EXISTS can_manage_masters BOOLEAN DEFAULT FALSE",
              "ALTER TABLE user_permissions ADD COLUMN IF NOT EXISTS can_view_accounts BOOLEAN DEFAULT FALSE",
              "ALTER TABLE user_permissions ADD COLUMN IF NOT EXISTS can_manage_accounts BOOLEAN DEFAULT FALSE",
              "ALTER TABLE user_permissions ADD COLUMN IF NOT EXISTS can_view_profit_report BOOLEAN DEFAULT FALSE",
              "UPDATE users SET is_active=TRUE WHERE is_active IS NULL"]:
        try: cur.execute(m)
        except Exception: conn.rollback()
    conn.commit(); conn.close(); print("ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ Phase 2 DB ready.")

# App startup pe init_db run karo
with app.app_context():
    try:
        init_db()
    except Exception as e:
        print(f"Init DB error: {e}")

# STATIC
@app.route('/')
def index(): return send_from_directory('static','index.html')

@app.route('/uploads/<path:filename>')
@login_required
def serve_upload(filename):
    if filename.startswith('doc_') and not is_admin():
        return jsonify({'error':'Only Admin/Super Admin can open uploaded document files'}), 403
    return send_from_directory(UPLOAD_FOLDER, filename)

# AUTH
@app.route('/api/login', methods=['POST'])
def login():
    d = request.json or {}
    ip = request.remote_addr or 'unknown'
    if d.get('username','') == 'admin' and d.get('password','') == 'sky@2024':
        try:
            q("CREATE TABLE IF NOT EXISTS tenants (id SERIAL PRIMARY KEY, name TEXT UNIQUE NOT NULL, status TEXT DEFAULT 'Active', subscription_start DATE DEFAULT CURRENT_DATE, subscription_end DATE, notes TEXT, created_at TIMESTAMP DEFAULT NOW())", commit=True)
            q("INSERT INTO tenants (name,status,notes) VALUES (%s,%s,%s) ON CONFLICT (name) DO NOTHING", ('Sky Eduworld','Active','Default tenant for existing data'), commit=True)
            q("ALTER TABLE users ADD COLUMN IF NOT EXISTS tenant_id INTEGER", commit=True)
            tenant = q("SELECT id FROM tenants WHERE name='Sky Eduworld' LIMIT 1", one=True)
            admin = q("SELECT id FROM users WHERE username='admin'", one=True)
            if admin:
                q("UPDATE users SET password=%s,role='Super Admin',is_active=TRUE,tenant_id=COALESCE(tenant_id,%s),failed_logins=0 WHERE id=%s", (hash_pw('sky@2024'),tenant['id'] if tenant else None,admin['id']), commit=True)
            else:
                q("INSERT INTO users (tenant_id,username,password,full_name,role,is_active) VALUES (%s,%s,%s,%s,%s,TRUE)", (tenant['id'] if tenant else None,'admin',hash_pw('sky@2024'),'Admin','Super Admin'), commit=True)
        except Exception:
            pass
    user = q("SELECT * FROM users WHERE username=%s", (d.get('username',''),), one=True)
    if user and user.get('username') == 'admin' and d.get('password','') == 'sky@2024' and user.get('password') != hash_pw('sky@2024'):
        q("UPDATE users SET password=%s,is_active=TRUE,tenant_id=COALESCE(tenant_id,(SELECT id FROM tenants WHERE name='Sky Eduworld' LIMIT 1)) WHERE id=%s", (hash_pw('sky@2024'),user['id']), commit=True)
        user = q("SELECT * FROM users WHERE id=%s", (user['id'],), one=True)
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
    session['user_id'] = user['id']; session['username'] = user['username']; session['tenant_id'] = user.get('tenant_id')
    session['role'] = user['role']; session['session_token'] = token
    perms = get_user_perms(user['id']); univs = get_user_univs(user['id'])
    active_sess = get_active_session()
    return jsonify({'success':True,'user':{'id':user['id'],'username':user['username'],
        'full_name':user['full_name'],'role':user['role'],'tenant_id':user.get('tenant_id'),'permissions':perms,
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
    user = q("SELECT id,tenant_id,username,full_name,role,last_login FROM users WHERE id=%s", (session['user_id'],), one=True)
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
        sql += " AND (s.student_code ILIKE %s OR s.name ILIKE %s OR s.father ILIKE %s OR s.mobile ILIKE %s OR s.course ILIKE %s OR s.university ILIKE %s OR s.enroll_no ILIKE %s)"
        p = f'%{search}%'; params += [p,p,p,p,p,p,p]
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
    partial = bool(d.get('is_partial'))
    if partial and not (is_super_admin() or get_user_perms(uid).get('can_save_partial_student')): return jsonify({'error':'Permission denied: can_save_partial_student'}), 403
    if not partial and (not d.get('name') or not d.get('mobile') or not d.get('father')): return jsonify({'error':'Name, Mobile and Father Name are required'}), 400
    univs = get_user_univs(uid)
    if univs and d.get('university') not in univs: return jsonify({'error':'Not assigned to this university'}), 403
    active_sess = get_active_session()
    tid = current_tenant_id()
    row = q_ret("""INSERT INTO students (tenant_id,created_by,session_id,name,father,mother,dob,gender,mobile,email,aadhar,address,course,subject,university,batch,enroll_no,roll_no,adm_date,remarks,total_fee,paid,univ_fee,pay_mode,utr,doc_notes,status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
               (tid, uid, active_sess['id'] if active_sess else None, d.get('name'), d.get('father'), d.get('mother'),
                d.get('dob') or None, d.get('gender'), d.get('mobile'), d.get('email'), d.get('aadhar'), d.get('address'),
                d.get('course'), d.get('subject'), d.get('university'), d.get('batch'), d.get('enroll_no'), d.get('roll_no'),
                d.get('adm_date') or None, d.get('remarks'), d.get('total_fee',0), d.get('paid',0),
                d.get('univ_fee',0), d.get('pay_mode'), d.get('utr'), d.get('doc_notes'), 'Draft' if partial else 'Active'))
    paid = float(d.get('paid',0) or 0)
    if paid > 0 and row:
        conn = get_db(); cur = conn.cursor()
        cur.execute("INSERT INTO fee_payments (tenant_id,student_id,recorded_by,amount,fee_type,pay_mode,ref_no,pay_date) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (tid,row['id'],uid,paid,'Initial Payment',d.get('pay_mode'),d.get('utr'),d.get('adm_date') or None))
        conn.commit()
    if row:
        assign_student_code(row['id'])
        row = q('SELECT * FROM students WHERE id=%s', (row['id'],), one=True)
    if row and float(d.get('univ_fee',0) or 0) > 0:
        q_ret("INSERT INTO university_payables (tenant_id,student_id,created_by,university,student,amount,fee_type,status,remarks) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id", (tid,row['id'],uid,d.get('university'),d.get('name'),d.get('univ_fee',0),'Tuition','Pending','Auto from student admission'))
    if row and d.get('guide_id'):
        try:
            assign_student_to_guide(row['id'], d.get('guide_id'))
        except ValueError as e:
            return jsonify({'error':str(e)}), 400
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
    university_payments = q("SELECT * FROM university_payables WHERE student_id=%s ORDER BY id DESC", (sid,))
    associates = q("SELECT * FROM associates WHERE LOWER(TRIM(COALESCE(student,'')))=LOWER(TRIM(%s)) ORDER BY id DESC", (row.get('name'),))
    references = q("SELECT * FROM references_ WHERE LOWER(TRIM(COALESCE(student,'')))=LOWER(TRIM(%s)) ORDER BY id DESC", (row.get('name'),))
    followups = q("SELECT f.*, u.full_name AS by_name FROM follow_ups f LEFT JOIN users u ON u.id=f.created_by WHERE f.student_id=%s ORDER BY f.created_at DESC", (sid,))
    docs = q("SELECT * FROM documents WHERE student_id=%s ORDER BY id DESC", (sid,))
    guide = q("SELECT g.*, u.name AS university FROM guide_students gs JOIN guides g ON g.id=gs.guide_id JOIN universities u ON u.id=g.university_id WHERE gs.student_id=%s ORDER BY gs.id DESC LIMIT 1", (sid,), one=True)
    row['photo'] = serialize(photo) if photo else None
    row['payments'] = [serialize(r) for r in payments]
    row['installments'] = [serialize(r) for r in installments]
    row['university_payments'] = [serialize(r) for r in university_payments]
    row['associates'] = [serialize(r) for r in associates]
    row['references'] = [serialize(r) for r in references]
    row['followups'] = [serialize(r) for r in followups]
    row['documents'] = [serialize(r) for r in docs]
    row['guide'] = serialize_guide(guide) if guide else None
    return jsonify(serialize(row))

def cleanup_student_related_rows(student_ids=None, student_names=None):
    student_ids = list(dict.fromkeys([int(x) for x in (student_ids or []) if x]))
    student_names = list(dict.fromkeys([str(n).strip() for n in (student_names or []) if str(n or '').strip()]))
    deleted = 0
    if student_ids:
        for sql in [
            "DELETE FROM follow_ups WHERE student_id = ANY(%s)",
            "DELETE FROM documents WHERE student_id = ANY(%s)",
            "DELETE FROM student_photos WHERE student_id = ANY(%s)",
            "DELETE FROM fee_payments WHERE student_id = ANY(%s)",
            "DELETE FROM fee_installments WHERE student_id = ANY(%s)",
            "DELETE FROM university_payables WHERE student_id = ANY(%s)",
        ]:
            try:
                deleted += q(sql, (student_ids,), commit=True)
            except Exception:
                pass
    if student_names:
        normalized = [n.strip().lower() for n in student_names if n.strip()]
        deleted += q("DELETE FROM expenses WHERE LOWER(TRIM(COALESCE(student,''))) = ANY(%s)", (normalized,), commit=True)
        deleted += q("DELETE FROM documents WHERE LOWER(TRIM(COALESCE(student,''))) = ANY(%s)", (normalized,), commit=True)
        assoc_parent_ids = [r['id'] for r in q("SELECT id FROM associates WHERE parent_id IS NULL AND LOWER(TRIM(COALESCE(student,''))) = ANY(%s)", (normalized,))]
        ref_parent_ids = [r['id'] for r in q("SELECT id FROM references_ WHERE parent_id IS NULL AND LOWER(TRIM(COALESCE(student,''))) = ANY(%s)", (normalized,))]
        if assoc_parent_ids:
            deleted += q("DELETE FROM associates WHERE parent_id = ANY(%s)", (assoc_parent_ids,), commit=True)
            deleted += q("DELETE FROM associates WHERE id = ANY(%s)", (assoc_parent_ids,), commit=True)
        if ref_parent_ids:
            deleted += q("DELETE FROM references_ WHERE parent_id = ANY(%s)", (ref_parent_ids,), commit=True)
            deleted += q("DELETE FROM references_ WHERE id = ANY(%s)", (ref_parent_ids,), commit=True)
    return deleted

@app.route('/api/students/<int:sid>', methods=['PUT'])
@login_required
@require_perm('can_edit_student')
def update_student(sid):
    uid = session['user_id']; fs, fp = student_filter(uid)
    if not q(f"SELECT id FROM students WHERE id=%s {fs}", [sid]+list(fp), one=True):
        return jsonify({'error':'Not found or access denied'}), 404
    d = request.json or {}
    row = q_ret("""UPDATE students SET name=%s,father=%s,mother=%s,dob=%s,gender=%s,mobile=%s,email=%s,aadhar=%s,address=%s,course=%s,subject=%s,university=%s,batch=%s,enroll_no=%s,roll_no=%s,adm_date=%s,remarks=%s,total_fee=%s,univ_fee=%s,pay_mode=%s,utr=%s,doc_notes=%s,status=%s WHERE id=%s RETURNING *""",
               (d.get('name'),d.get('father'),d.get('mother'),d.get('dob') or None,d.get('gender'),d.get('mobile'),d.get('email'),d.get('aadhar'),d.get('address'),d.get('course'),d.get('subject'),d.get('university'),d.get('batch'),d.get('enroll_no'),d.get('roll_no'),d.get('adm_date') or None,d.get('remarks'),d.get('total_fee',0),d.get('univ_fee',0),d.get('pay_mode'),d.get('utr'),d.get('doc_notes'),d.get('status','Active'),sid))
    try:
        assign_student_to_guide(sid, d.get('guide_id'))
    except ValueError as e:
        return jsonify({'error':str(e)}), 400
    log_action('Edit','Student',sid,d.get('name'))
    return jsonify(serialize(row))

@app.route('/api/students/<int:sid>', methods=['DELETE'])
@login_required
@require_perm('can_delete_student')
def delete_student(sid):
    uid = session['user_id']; fs, fp = student_filter(uid)
    st = q(f"SELECT id,name FROM students WHERE id=%s {fs}", [sid]+list(fp), one=True)
    if not st:
        return jsonify({'error':'Not found or access denied'}), 404
    cleanup_student_related_rows([sid], [st.get('name')])
    log_action('Delete','Student',sid)
    q("DELETE FROM students WHERE id=%s", (sid,), commit=True)
    return jsonify({'success':True})


@app.route('/api/students/bulk-delete', methods=['POST'])
@login_required
@require_perm('can_delete_student')
def bulk_delete_students():
    d = request.json or {}
    uid = session['user_id']
    fs, fp = student_filter(uid, 's')
    sql = f"SELECT s.id,s.name FROM students s WHERE TRUE {fs}"
    params = list(fp)
    if d.get('university'):
        sql += " AND s.university=%s"; params.append(d.get('university'))
    if d.get('status'):
        sql += " AND s.status=%s"; params.append(d.get('status'))
    rows = q(sql, params)
    ids = [r['id'] for r in rows]
    if not ids:
        return jsonify({'success': True, 'deleted': 0})
    cleanup_student_related_rows(ids, [r.get('name') for r in rows])
    q("DELETE FROM students WHERE id = ANY(%s)", (ids,), commit=True)
    log_action('Bulk Delete', 'Student', None, f'{len(ids)} students')
    return jsonify({'success': True, 'deleted': len(ids)})

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
@require_perm('can_view_payments')
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
    installment_id = d.get('installment_id') or None
    if installment_id and not q("SELECT id FROM fee_installments WHERE id=%s AND student_id=%s", (installment_id, sid), one=True):
        return jsonify({'error':'Selected fee breakup not found'}), 404
    cur.execute("INSERT INTO fee_payments (student_id,recorded_by,installment_id,amount,fee_type,pay_mode,ref_no,pay_date,remarks) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (sid,uid,installment_id,amount,d.get('fee_type','Tuition Fee'),d.get('pay_mode','Cash'),d.get('ref_no',''),d.get('pay_date') or date.today().isoformat(),d.get('remarks','')))
    if d.get('installment_id'):
        cur.execute("""UPDATE fee_installments
                       SET status=CASE WHEN COALESCE((SELECT SUM(amount) FROM fee_payments WHERE installment_id=%s),0) >= amount THEN 'Paid' ELSE 'Pending' END,
                           paid_date=CASE WHEN COALESCE((SELECT SUM(amount) FROM fee_payments WHERE installment_id=%s),0) >= amount THEN CURRENT_DATE ELSE NULL END
                       WHERE id=%s""", (d['installment_id'], d['installment_id'], d['installment_id']))
    conn.commit()
    log_action('Payment','Fee',sid,f"Rs{amount}")
    if new_paid >= float(student['total_fee']): notify_user(uid,'Fee Cleared!',f"{student['name']} ka poora fee jama ho gaya.",'success')
    updated = q("SELECT * FROM students WHERE id=%s", (sid,), one=True)
    return jsonify({'success':True,'new_paid':new_paid,'student':serialize(updated)})

@app.route('/api/students/<int:sid>/payments/<int:pid>', methods=['PUT','DELETE'])
@login_required
@require_perm('can_add_payment')
def payment_item(sid, pid):
    uid=session['user_id']; fs, fp = student_filter(uid)
    student=q(f"SELECT * FROM students WHERE id=%s {fs}", [sid]+list(fp), one=True)
    if not student: return jsonify({'error':'Not found or access denied'}), 404
    old=q("SELECT * FROM fee_payments WHERE id=%s AND student_id=%s", (pid,sid), one=True)
    if not old: return jsonify({'error':'Payment not found'}), 404
    if request.method == 'DELETE':
        q("DELETE FROM fee_payments WHERE id=%s", (pid,), commit=True)
    else:
        d=request.json or {}; amount=float(d.get('amount',0) or 0)
        if amount<=0: return jsonify({'error':'Valid amount required'}), 400
        q("UPDATE fee_payments SET amount=%s, fee_type=%s, pay_mode=%s, ref_no=%s, pay_date=%s, remarks=%s WHERE id=%s", (amount,d.get('fee_type','Tuition Fee'),d.get('pay_mode','Cash'),d.get('ref_no',''),d.get('pay_date') or date.today().isoformat(),d.get('remarks',''),pid), commit=True)
    if old.get('installment_id'):
        q("""UPDATE fee_installments
             SET status=CASE WHEN COALESCE((SELECT SUM(amount) FROM fee_payments WHERE installment_id=%s),0) >= amount THEN 'Paid' ELSE 'Pending' END,
                 paid_date=CASE WHEN COALESCE((SELECT SUM(amount) FROM fee_payments WHERE installment_id=%s),0) >= amount THEN CURRENT_DATE ELSE NULL END
             WHERE id=%s""", (old.get('installment_id'), old.get('installment_id'), old.get('installment_id')), commit=True)
    total=q("SELECT COALESCE(SUM(amount),0) AS paid FROM fee_payments WHERE student_id=%s", (sid,), one=True)['paid']
    q("UPDATE students SET paid=%s WHERE id=%s", (total,sid), commit=True)
    updated=q("SELECT * FROM students WHERE id=%s", (sid,), one=True)
    return jsonify({'success':True,'student':serialize(updated)})


# INSTALLMENTS
def refresh_student_fee_total(sid):
    total = q("SELECT COALESCE(SUM(amount),0) AS total FROM fee_installments WHERE student_id=%s", (sid,), one=True)['total']
    q("UPDATE students SET total_fee=%s WHERE id=%s", (total, sid), commit=True)
    return total

@app.route('/api/students/<int:sid>/installments', methods=['GET'])
@login_required
def get_installments(sid):
    return jsonify([serialize(r) for r in q("SELECT * FROM fee_installments WHERE student_id=%s ORDER BY due_date", (sid,))])

@app.route('/api/students/<int:sid>/installments', methods=['POST'])
@login_required
def add_installment(sid):
    uid = session['user_id']; fs, fp = student_filter(uid)
    if not q(f"SELECT id FROM students WHERE id=%s {fs}", [sid]+list(fp), one=True):
        return jsonify({'error':'Not found or access denied'}), 404
    d = request.json or {}
    amount = float(d.get('amount',0) or 0)
    if amount <= 0:
        return jsonify({'error':'Valid amount required'}), 400
    row = q_ret("INSERT INTO fee_installments (student_id,created_by,amount,fee_type,due_date,remarks) VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",
                (sid, uid, amount, d.get('fee_type') or 'Tuition Fee', d.get('due_date') or None, d.get('remarks')))
    total = refresh_student_fee_total(sid)
    out = serialize(row); out['student_total_fee'] = total
    log_action('Add','Fee Breakup',sid,d.get('fee_type') or 'Tuition Fee')
    return jsonify(out), 201

@app.route('/api/students/<int:sid>/installments/<int:iid>', methods=['PUT','DELETE'])
@login_required
def installment_item(sid, iid):
    uid = session['user_id']; fs, fp = student_filter(uid)
    if not q(f"SELECT id FROM students WHERE id=%s {fs}", [sid]+list(fp), one=True):
        return jsonify({'error':'Not found or access denied'}), 404
    old = q("SELECT * FROM fee_installments WHERE id=%s AND student_id=%s", (iid, sid), one=True)
    if not old:
        return jsonify({'error':'Fee breakup not found'}), 404
    if request.method == 'DELETE':
        q("DELETE FROM fee_installments WHERE id=%s", (iid,), commit=True)
        total = refresh_student_fee_total(sid)
        log_action('Delete','Fee Breakup',sid,old.get('fee_type'))
        return jsonify({'success':True,'student_total_fee':total})
    d = request.json or {}
    amount = float(d.get('amount',0) or 0)
    if amount <= 0:
        return jsonify({'error':'Valid amount required'}), 400
    row = q_ret("UPDATE fee_installments SET amount=%s, fee_type=%s, due_date=%s, remarks=%s, status=%s WHERE id=%s AND student_id=%s RETURNING *",
                (amount, d.get('fee_type') or 'Tuition Fee', d.get('due_date') or None, d.get('remarks'), d.get('status') or old.get('status') or 'Pending', iid, sid))
    total = refresh_student_fee_total(sid)
    out = serialize(row); out['student_total_fee'] = total
    log_action('Edit','Fee Breakup',sid,d.get('fee_type') or old.get('fee_type'))
    return jsonify(out)

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
        assign_student_code(row['id'])
        row = q('SELECT * FROM students WHERE id=%s', (row['id'],), one=True)
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
    if is_super_admin(): rows = q("SELECT a.*, u.full_name AS by_name FROM associates a LEFT JOIN users u ON u.id=a.created_by WHERE a.parent_id IS NULL ORDER BY a.id DESC")
    else: rows = q("SELECT a.*, u.full_name AS by_name FROM associates a LEFT JOIN users u ON u.id=a.created_by WHERE a.parent_id IS NULL AND a.created_by=%s ORDER BY a.id DESC", (uid,))
    return jsonify([serialize(r) for r in rows])

@app.route('/api/associates', methods=['POST'])
@login_required
@require_perm('can_manage_associates')
def add_associate():
    d = request.json or {}
    row = q_ret("INSERT INTO associates (created_by,name,phone,student,work_done,amount,paid_amount,pay_date,pay_mode,utr,status,notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                (session['user_id'],d.get('name'),d.get('phone'),d.get('student'),d.get('work_done'),d.get('amount',0),d.get('paid_amount',d.get('amount',0)),d.get('pay_date') or None,d.get('pay_mode','Cash'),d.get('utr'),d.get('status','Paid'),d.get('notes')))
    return jsonify(serialize(row)), 201

@app.route('/api/associates/<int:aid>', methods=['PUT','DELETE'])
@login_required
@require_perm('can_manage_associates')
def delete_associate(aid):
    if not is_super_admin():
        if not q("SELECT id FROM associates WHERE id=%s AND created_by=%s", (aid,session['user_id']), one=True): return jsonify({'error':'Access denied'}), 403
    if request.method == 'PUT':
        d = request.json or {}
        row = q_ret("UPDATE associates SET name=%s,phone=%s,student=%s,work_done=%s,amount=%s,paid_amount=%s,pay_date=%s,pay_mode=%s,utr=%s,status=%s,notes=%s WHERE id=%s RETURNING *",
                    (d.get('name'),d.get('phone'),d.get('student'),d.get('work_done'),d.get('amount',0),d.get('paid_amount',0),d.get('pay_date') or None,d.get('pay_mode','Cash'),d.get('utr'),d.get('status','Pending'),d.get('notes'),aid))
        return jsonify(serialize(row))
    q("DELETE FROM associates WHERE id=%s", (aid,), commit=True); return jsonify({'success':True})

@app.route('/api/associates/bulk-delete', methods=['POST'])
@login_required
@require_perm('can_manage_associates')
def bulk_delete_associates():
    d = request.json or {}
    ids = [int(x) for x in (d.get('ids') or []) if str(x).isdigit()]
    if not ids or d.get('confirm') != 'DELETE': return jsonify({'error':'Confirm DELETE and select records'}), 400
    if is_super_admin():
        deleted = q("DELETE FROM associates WHERE id = ANY(%s)", (ids,), commit=True)
    else:
        deleted = q("DELETE FROM associates WHERE id = ANY(%s) AND created_by=%s", (ids,session['user_id']), commit=True)
    return jsonify({'success':True,'deleted':deleted})

# REFERENCES
@app.route('/api/references', methods=['GET'])
@login_required
@require_perm('can_view_references')
def get_references():
    uid = session['user_id']
    if is_super_admin(): rows = q("SELECT r.*, u.full_name AS by_name FROM references_ r LEFT JOIN users u ON u.id=r.created_by WHERE r.parent_id IS NULL ORDER BY r.id DESC")
    else: rows = q("SELECT r.*, u.full_name AS by_name FROM references_ r LEFT JOIN users u ON u.id=r.created_by WHERE r.parent_id IS NULL AND r.created_by=%s ORDER BY r.id DESC", (uid,))
    return jsonify([serialize(r) for r in rows])

@app.route('/api/references', methods=['POST'])
@login_required
@require_perm('can_manage_references')
def add_reference():
    d = request.json or {}
    row = q_ret("INSERT INTO references_ (created_by,name,phone,student,university,amount,paid_amount,pay_date,pay_mode,utr,status,notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                (session['user_id'],d.get('name'),d.get('phone'),d.get('student'),d.get('university'),d.get('amount',0),d.get('paid_amount',d.get('amount',0)),d.get('pay_date') or None,d.get('pay_mode','Cash'),d.get('utr'),d.get('status','Paid'),d.get('notes')))
    return jsonify(serialize(row)), 201

@app.route('/api/references/<int:rid>', methods=['PUT','DELETE'])
@login_required
@require_perm('can_manage_references')
def delete_reference(rid):
    if not is_super_admin():
        if not q("SELECT id FROM references_ WHERE id=%s AND created_by=%s", (rid,session['user_id']), one=True): return jsonify({'error':'Access denied'}), 403
    if request.method == 'PUT':
        d = request.json or {}
        row = q_ret("UPDATE references_ SET name=%s,phone=%s,student=%s,university=%s,amount=%s,paid_amount=%s,pay_date=%s,pay_mode=%s,utr=%s,status=%s,notes=%s WHERE id=%s RETURNING *",
                    (d.get('name'),d.get('phone'),d.get('student'),d.get('university'),d.get('amount',0),d.get('paid_amount',0),d.get('pay_date') or None,d.get('pay_mode','Cash'),d.get('utr'),d.get('status','Pending'),d.get('notes'),rid))
        return jsonify(serialize(row))
    q("DELETE FROM references_ WHERE id=%s", (rid,), commit=True); return jsonify({'success':True})

@app.route('/api/references/bulk-delete', methods=['POST'])
@login_required
@require_perm('can_manage_references')
def bulk_delete_references():
    d = request.json or {}
    ids = [int(x) for x in (d.get('ids') or []) if str(x).isdigit()]
    if not ids or d.get('confirm') != 'DELETE': return jsonify({'error':'Confirm DELETE and select records'}), 400
    if is_super_admin():
        deleted = q("DELETE FROM references_ WHERE id = ANY(%s)", (ids,), commit=True)
    else:
        deleted = q("DELETE FROM references_ WHERE id = ANY(%s) AND created_by=%s", (ids,session['user_id']), commit=True)
    return jsonify({'success':True,'deleted':deleted})


@app.route('/api/associates/<int:aid>/parts', methods=['GET','POST'])
@login_required
@require_perm('can_manage_associates')
def add_associate_part(aid):
    uid=session['user_id']; parent=q("SELECT * FROM associates WHERE id=%s AND parent_id IS NULL", (aid,), one=True)
    if not parent: return jsonify({'error':'Associate record not found'}), 404
    if not is_super_admin() and parent.get('created_by') != uid: return jsonify({'error':'Access denied'}), 403
    if request.method == 'GET': return jsonify([serialize(r) for r in q("SELECT * FROM associates WHERE parent_id=%s ORDER BY pay_date,id", (aid,))])
    d=request.json or {}; amt=float(d.get('amount',0) or 0)
    if amt<=0: return jsonify({'error':'Valid amount required'}), 400
    row=q_ret("INSERT INTO associates (created_by,parent_id,name,phone,student,work_done,amount,paid_amount,pay_date,pay_mode,utr,status,notes) VALUES (%s,%s,%s,%s,%s,%s,0,%s,%s,%s,%s,%s,%s) RETURNING *", (uid,aid,parent['name'],parent.get('phone'),parent.get('student'),parent.get('work_done'),amt,d.get('pay_date') or date.today().isoformat(),d.get('pay_mode','Cash'),d.get('utr'),d.get('status','Paid'),d.get('notes')))
    total=q("SELECT COALESCE(SUM(paid_amount),0) AS t FROM associates WHERE parent_id=%s", (aid,), one=True)['t']
    q("UPDATE associates SET paid_amount=%s,status=%s WHERE id=%s", (total,'Paid' if float(total)>=float(parent.get('amount') or 0) else 'Partial',aid), commit=True)
    return jsonify(serialize(row)),201

@app.route('/api/references/<int:rid>/parts', methods=['GET','POST'])
@login_required
@require_perm('can_manage_references')
def add_reference_part(rid):
    uid=session['user_id']; parent=q("SELECT * FROM references_ WHERE id=%s AND parent_id IS NULL", (rid,), one=True)
    if not parent: return jsonify({'error':'Reference record not found'}), 404
    if not is_super_admin() and parent.get('created_by') != uid: return jsonify({'error':'Access denied'}), 403
    if request.method == 'GET': return jsonify([serialize(r) for r in q("SELECT * FROM references_ WHERE parent_id=%s ORDER BY pay_date,id", (rid,))])
    d=request.json or {}; amt=float(d.get('amount',0) or 0)
    if amt<=0: return jsonify({'error':'Valid amount required'}), 400
    row=q_ret("INSERT INTO references_ (created_by,parent_id,name,phone,student,university,amount,paid_amount,pay_date,pay_mode,utr,status,notes) VALUES (%s,%s,%s,%s,%s,%s,0,%s,%s,%s,%s,%s,%s) RETURNING *", (uid,rid,parent['name'],parent.get('phone'),parent.get('student'),parent.get('university'),amt,d.get('pay_date') or date.today().isoformat(),d.get('pay_mode','Cash'),d.get('utr'),d.get('status','Paid'),d.get('notes')))
    total=q("SELECT COALESCE(SUM(paid_amount),0) AS t FROM references_ WHERE parent_id=%s", (rid,), one=True)['t']
    q("UPDATE references_ SET paid_amount=%s,status=%s WHERE id=%s", (total,'Paid' if float(total)>=float(parent.get('amount') or 0) else 'Partial',rid), commit=True)
    return jsonify(serialize(row)),201

# UNIVERSITIES
@app.route('/api/universities', methods=['GET'])
@login_required
def get_universities():
    uid = session['user_id']; assigned = get_user_univs(uid)
    tf,tp = tenant_filter('u')
    rows = q("SELECT u.id, u.name, u.state, u.color, u.is_active, u.tenant_id, COUNT(s.id)::int AS student_count FROM universities u LEFT JOIN students s ON s.university=u.name AND s.tenant_id=u.tenant_id WHERE TRUE"+tf+" GROUP BY u.id,u.name,u.state,u.color,u.is_active,u.tenant_id ORDER BY u.name", tp)
    if assigned: rows = [r for r in rows if r['name'] in assigned]
    return jsonify(rows)

GUIDE_LIMITS = {'Professor': 8, 'Associate Professor': 6, 'Assistant Professor': 4}

def guide_capacity(designation):
    return GUIDE_LIMITS.get((designation or '').strip(), 0)

def norm_text(v):
    return ' '.join(str(v or '').strip().lower().split())

def is_phd_course(v):
    return 'phd' in norm_text(v).replace('.', '')

def serialize_guide(r):
    g = serialize(r)
    cap = guide_capacity(g.get('designation'))
    linked = q("SELECT s.id,s.student_code,s.name,s.course,s.subject,s.university FROM guide_students gs JOIN students s ON s.id=gs.student_id WHERE gs.guide_id=%s ORDER BY s.name", (g.get('id'),)) if g.get('id') else []
    used = len(linked) if linked else int(g.get('assigned_students') or 0)
    g['capacity'] = cap
    g['assigned_students'] = used
    g['available_seats'] = max(cap - used, 0)
    g['students'] = [serialize(s) for s in linked]
    if g.get('file_path') and is_admin():
        g['file_url'] = f'/uploads/{g["file_path"]}'
    return g

def sync_guide_students(gid, student_ids):
    gid = int(gid)
    guide = q("SELECT * FROM guides WHERE id=%s", (gid,), one=True)
    if not guide: return
    cap = guide_capacity(guide.get('designation'))
    ids = []
    for x in (student_ids or []):
        try:
            sid = int(x)
            if sid not in ids: ids.append(sid)
        except Exception:
            pass
    if cap and len(ids) > cap:
        raise ValueError(f"{guide.get('designation')} ke under max {cap} students allowed hain")
    if ids:
        rows = q("SELECT id,name,course,subject FROM students WHERE id = ANY(%s)", (ids,))
        found = {int(r['id']): r for r in rows}
        guide_subject = norm_text(guide.get('subject'))
        for sid in ids:
            st = found.get(int(sid))
            if not st or not is_phd_course(st.get('course')) or norm_text(st.get('subject')) != guide_subject:
                raise ValueError('Sirf guide subject ke Ph.D students add ho sakte hain')
    affected = [gid] + [r['guide_id'] for r in q("SELECT DISTINCT guide_id FROM guide_students WHERE student_id = ANY(%s)", (ids,))] if ids else [gid]
    q("DELETE FROM guide_students WHERE guide_id=%s", (gid,), commit=True)
    for sid in ids:
        q("DELETE FROM guide_students WHERE student_id=%s AND guide_id<>%s", (sid,gid), commit=True)
        q("INSERT INTO guide_students (guide_id,student_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (gid,sid), commit=True)
    for agid in list(dict.fromkeys(affected)):
        total = q("SELECT COUNT(*) AS c FROM guide_students WHERE guide_id=%s", (agid,), one=True)['c']
        q("UPDATE guides SET assigned_students=%s WHERE id=%s", (int(total or 0),agid), commit=True)

def assign_student_to_guide(student_id, guide_id):
    old_rows = q("SELECT guide_id FROM guide_students WHERE student_id=%s", (student_id,))
    old_ids = [r['guide_id'] for r in old_rows]
    if guide_id:
        gid = int(guide_id)
        guide = q("SELECT * FROM guides WHERE id=%s", (gid,), one=True)
        if not guide: return
        st = q("SELECT id,course,subject FROM students WHERE id=%s", (student_id,), one=True)
        if not st or not is_phd_course(st.get('course')) or norm_text(st.get('subject')) != norm_text(guide.get('subject')):
            raise ValueError('Sirf guide subject ke Ph.D student assign ho sakte hain')
        current = q("SELECT COUNT(*) AS c FROM guide_students WHERE guide_id=%s AND student_id<>%s", (gid,student_id), one=True)['c']
        cap = guide_capacity(guide.get('designation'))
        if cap and int(current or 0) >= cap:
            raise ValueError(f"{guide.get('name')} guide ki capacity full hai")
    q("DELETE FROM guide_students WHERE student_id=%s", (student_id,), commit=True)
    if guide_id:
        q("INSERT INTO guide_students (guide_id,student_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (int(guide_id),student_id), commit=True)
    for gid in list(dict.fromkeys(old_ids + ([int(guide_id)] if guide_id else []))):
        total = q("SELECT COUNT(*) AS c FROM guide_students WHERE guide_id=%s", (gid,), one=True)['c']
        q("UPDATE guides SET assigned_students=%s WHERE id=%s", (int(total or 0),gid), commit=True)

@app.route('/api/guides', methods=['GET'])
@login_required
def get_guides():
    uid = session['user_id']; assigned = get_user_univs(uid)
    tf,tp = tenant_filter('g')
    rows = q("SELECT g.*, u.name AS university, u.state AS university_state FROM guides g JOIN universities u ON u.id=g.university_id WHERE TRUE"+tf+" ORDER BY u.name,g.name", tp)
    if assigned: rows = [r for r in rows if r.get('university') in assigned]
    return jsonify([serialize_guide(r) for r in rows])

@app.route('/api/universities/<int:uid_>/detail', methods=['GET'])
@login_required
def university_detail(uid_):
    tf,tp = tenant_filter('u')
    u = q("SELECT u.id, u.tenant_id, u.name, u.state, u.color, u.is_active, COUNT(s.id)::int AS student_count FROM universities u LEFT JOIN students s ON s.university=u.name AND s.tenant_id=u.tenant_id WHERE u.id=%s"+tf+" GROUP BY u.id,u.tenant_id,u.name,u.state,u.color,u.is_active", [uid_]+tp, one=True)
    if not u: return jsonify({'error':'University not found'}), 404
    assigned = get_user_univs(session['user_id'])
    if assigned and u['name'] not in assigned: return jsonify({'error':'Permission denied'}), 403
    students = q("SELECT id,student_code,name,course,subject,batch,mobile,status FROM students WHERE university=%s AND tenant_id=%s ORDER BY id DESC LIMIT 50", (u['name'],u['tenant_id']))
    guides = q("SELECT * FROM guides WHERE university_id=%s AND tenant_id=%s ORDER BY name", (uid_,u['tenant_id']))
    data = serialize(u)
    data['students'] = [serialize(r) for r in students]
    data['guides'] = [serialize_guide(r) for r in guides]
    data['guide_capacity_total'] = sum(g['capacity'] for g in data['guides'])
    data['guide_assigned_total'] = sum(int(g.get('assigned_students') or 0) for g in data['guides'])
    return jsonify(data)

@app.route('/api/universities', methods=['POST'])
@login_required
@require_perm('can_manage_universities')
def add_university():
    d = request.json or {}
    try: q_ret("INSERT INTO universities (tenant_id,name,state,color) VALUES (%s,%s,%s,%s) RETURNING id", (current_tenant_id(),d.get('name'),d.get('state'),d.get('color','#1A6CF6')))
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

@app.route('/api/universities/<int:uid_>/guides', methods=['POST'])
@login_required
@require_perm('can_manage_universities')
def add_guide(uid_):
    d = request.form if request.form else (request.json or {})
    if not d.get('name'): return jsonify({'error':'Guide name required'}), 400
    if guide_capacity(d.get('designation')) <= 0: return jsonify({'error':'Select valid designation'}), 400
    univ = q("SELECT tenant_id FROM universities WHERE id=%s", (uid_,), one=True)
    if not univ or not ensure_tenant_access(univ.get('tenant_id')): return jsonify({'error':'University access denied'}), 403
    row = q_ret("INSERT INTO guides (tenant_id,university_id,name,designation,department,subject,mobile,email,assigned_students,created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                (univ.get('tenant_id'),uid_,d.get('name'),d.get('designation'),d.get('department'),d.get('subject'),d.get('mobile'),d.get('email'),0,session['user_id']))
    try:
        sync_guide_students(row['id'], d.get('student_ids') or [])
    except ValueError as e:
        q("DELETE FROM guides WHERE id=%s", (row['id'],), commit=True)
        return jsonify({'error':str(e)}), 400
    row = q("SELECT * FROM guides WHERE id=%s", (row['id'],), one=True)
    return jsonify(serialize_guide(row)), 201

@app.route('/api/guides/<int:gid>', methods=['PUT'])
@login_required
@require_perm('can_manage_universities')
def update_guide(gid):
    d = request.form if request.form else (request.json or {})
    if guide_capacity(d.get('designation')) <= 0: return jsonify({'error':'Select valid designation'}), 400
    row = q_ret("UPDATE guides SET name=%s,designation=%s,department=%s,subject=%s,mobile=%s,email=%s,assigned_students=%s WHERE id=%s RETURNING *",
                (d.get('name'),d.get('designation'),d.get('department'),d.get('subject'),d.get('mobile'),d.get('email'),int(d.get('assigned_students') or 0),gid))
    if 'student_ids' in d:
        try:
            sync_guide_students(gid, d.get('student_ids') or [])
        except ValueError as e:
            return jsonify({'error':str(e)}), 400
        row = q("SELECT * FROM guides WHERE id=%s", (gid,), one=True)
    return jsonify(serialize_guide(row))

@app.route('/api/guides/<int:gid>/upload', methods=['POST'])
@login_required
@require_perm('can_manage_universities')
def upload_guide_file(gid):
    if 'file' not in request.files: return jsonify({'error':'No file'}), 400
    file = request.files['file']
    file.seek(0, os.SEEK_END); size = file.tell(); file.seek(0)
    if size > MAX_UPLOAD_BYTES: return jsonify({'error':'File size max 512 KB allowed'}), 400
    if not allowed_file(file.filename): return jsonify({'error':'Invalid type'}), 400
    ext = file.filename.rsplit('.',1)[1].lower()
    filename = f"guide_{gid}_{uuid.uuid4().hex[:8]}.{ext}"
    file.save(os.path.join(UPLOAD_FOLDER, filename))
    q("UPDATE guides SET file_path=%s,file_name=%s WHERE id=%s", (filename,file.filename,gid), commit=True)
    return jsonify({'success':True,'url':f'/uploads/{filename}'})

@app.route('/api/guides/<int:gid>', methods=['DELETE'])
@login_required
@require_perm('can_manage_universities')
def delete_guide(gid):
    row = q("SELECT file_path FROM guides WHERE id=%s", (gid,), one=True)
    if row and row.get('file_path'):
        try: os.remove(os.path.join(UPLOAD_FOLDER, row['file_path']))
        except: pass
    q("DELETE FROM guides WHERE id=%s", (gid,), commit=True)
    return jsonify({'success':True})

# ACADEMIC SESSIONS
@app.route('/api/sessions', methods=['GET'])
@login_required
def get_sessions():
    tf,tp = tenant_filter('', include_super=True)
    return jsonify([serialize(r) for r in q("SELECT * FROM academic_sessions WHERE TRUE"+tf+" ORDER BY id DESC", tp)])

@app.route('/api/sessions', methods=['POST'])
@login_required
@super_admin_required
def add_session():
    d = request.json or {}
    row = q_ret("INSERT INTO academic_sessions (tenant_id,name,start_date,end_date,is_active,created_by) VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",
                (current_tenant_id(),d.get('name'),d.get('start_date') or None,d.get('end_date') or None,False,session['user_id']))
    return jsonify(serialize(row)), 201

@app.route('/api/sessions/<int:sid>/activate', methods=['POST'])
@login_required
@super_admin_required
def activate_session(sid):
    tid=current_tenant_id()
    q("UPDATE academic_sessions SET is_active=FALSE WHERE tenant_id=%s", (tid,), commit=True)
    q("UPDATE academic_sessions SET is_active=TRUE WHERE id=%s AND tenant_id=%s", (sid,tid), commit=True)
    log_action('Activate','Session',sid)
    return jsonify({'success':True})

@app.route('/api/sessions/<int:sid>', methods=['DELETE'])
@login_required
@super_admin_required
def delete_session(sid):
    active = q("SELECT is_active FROM academic_sessions WHERE id=%s", (sid,), one=True)
    if active and active['is_active']: return jsonify({'error':'Cannot delete active session'}), 400
    q("DELETE FROM academic_sessions WHERE id=%s", (sid,), commit=True); return jsonify({'success':True})

# MASTER DATA
MASTER_TABLES = {'courses':'courses','subjects':'subjects','document-types':'document_types','fee-types':'fee_types','student-statuses':'student_statuses'}

def master_table(kind):
    table = MASTER_TABLES.get(kind)
    if not table: return None
    return table

@app.route('/api/masters/<kind>', methods=['GET'])
@login_required
def list_master(kind):
    table = master_table(kind)
    if not table: return jsonify({'error':'Invalid master'}), 404
    tf,tp = tenant_filter('', include_super=True)
    if table == 'subjects':
        rows = q("SELECT id,name,course_name,is_active,created_at FROM subjects WHERE TRUE"+tf+" ORDER BY name", tp)
    elif table == 'fee_types':
        rows = q("SELECT id,name,category,is_active,created_at FROM fee_types WHERE TRUE"+tf+" ORDER BY name", tp)
    elif table == 'document_types':
        rows = q("SELECT id,name,category,is_active,created_at FROM document_types WHERE TRUE"+tf+" ORDER BY name", tp)
    elif table == 'student_statuses':
        rows = q("SELECT id,name,category,is_active,created_at FROM student_statuses WHERE TRUE"+tf+" ORDER BY name", tp)
    else:
        rows = q("SELECT id,name,is_active,created_at FROM courses WHERE TRUE"+tf+" ORDER BY name", tp)
    return jsonify([serialize(r) for r in rows])

@app.route('/api/masters/<kind>', methods=['POST'])
@login_required
def add_master(kind):
    if kind == 'fee-types':
        if not (is_super_admin() or get_user_perms(session['user_id']).get('can_manage_fee_types')): return jsonify({'error':'Permission denied: can_manage_fee_types'}), 403
    elif not (is_super_admin() or get_user_perms(session['user_id']).get('can_manage_masters')): return jsonify({'error':'Permission denied: can_manage_masters'}), 403

    table = master_table(kind)
    if not table: return jsonify({'error':'Invalid master'}), 404
    d = request.json or {}; name = (d.get('name') or '').strip()
    if not name: return jsonify({'error':'Name required'}), 400
    try:
        if table == 'subjects':
            row = q_ret("INSERT INTO subjects (tenant_id,name,course_name,is_active) VALUES (%s,%s,%s,%s) RETURNING *", (current_tenant_id(),name,d.get('course_name'),d.get('is_active',True)))
        elif table == 'fee_types':
            row = q_ret("INSERT INTO fee_types (tenant_id,name,category,is_active) VALUES (%s,%s,%s,%s) RETURNING *", (current_tenant_id(),name,d.get('category','Student Fee'),d.get('is_active',True)))
        elif table == 'document_types':
            row = q_ret("INSERT INTO document_types (tenant_id,name,category,is_active) VALUES (%s,%s,%s,%s) RETURNING *", (current_tenant_id(),name,d.get('category','Student'),d.get('is_active',True)))
        elif table == 'student_statuses':
            row = q_ret("INSERT INTO student_statuses (tenant_id,name,category,is_active) VALUES (%s,%s,%s,%s) RETURNING *", (current_tenant_id(),name,d.get('category','Student'),d.get('is_active',True)))
        else:
            row = q_ret("INSERT INTO courses (tenant_id,name,is_active) VALUES (%s,%s,%s) RETURNING *", (current_tenant_id(),name,d.get('is_active',True)))
    except psycopg2.errors.UniqueViolation:
        get_db().rollback(); return jsonify({'error':'Already exists'}), 409
    log_action('Add','Master',row['id'] if row else None,f'{kind}: {name}')
    return jsonify(serialize(row)), 201

@app.route('/api/masters/<kind>/<int:mid>', methods=['PUT'])
@login_required
def update_master(kind, mid):
    if kind == 'fee-types':
        if not (is_super_admin() or get_user_perms(session['user_id']).get('can_manage_fee_types')): return jsonify({'error':'Permission denied: can_manage_fee_types'}), 403
    elif not (is_super_admin() or get_user_perms(session['user_id']).get('can_manage_masters')): return jsonify({'error':'Permission denied: can_manage_masters'}), 403
    table = master_table(kind)
    if not table: return jsonify({'error':'Invalid master'}), 404
    d = request.json or {}; name = (d.get('name') or '').strip()
    if not name: return jsonify({'error':'Name required'}), 400
    if table == 'subjects':
        row = q_ret("UPDATE subjects SET name=%s,course_name=%s,is_active=%s WHERE id=%s RETURNING *", (name,d.get('course_name'),d.get('is_active',True),mid))
    elif table == 'fee_types':
        row = q_ret("UPDATE fee_types SET name=%s,category=%s,is_active=%s WHERE id=%s RETURNING *", (name,d.get('category','Student Fee'),d.get('is_active',True),mid))
    elif table == 'document_types':
        row = q_ret("UPDATE document_types SET name=%s,category=%s,is_active=%s WHERE id=%s RETURNING *", (name,d.get('category','Student'),d.get('is_active',True),mid))
    elif table == 'student_statuses':
        row = q_ret("UPDATE student_statuses SET name=%s,category=%s,is_active=%s WHERE id=%s RETURNING *", (name,d.get('category','Student'),d.get('is_active',True),mid))
    else:
        row = q_ret("UPDATE courses SET name=%s,is_active=%s WHERE id=%s RETURNING *", (name,d.get('is_active',True),mid))
    if not row: return jsonify({'error':'Not found'}), 404
    log_action('Edit','Master',mid,f'{kind}: {name}')
    return jsonify(serialize(row))

@app.route('/api/masters/<kind>/<int:mid>', methods=['DELETE'])
@login_required
def delete_master(kind, mid):
    if kind == 'fee-types':
        if not (is_super_admin() or get_user_perms(session['user_id']).get('can_manage_fee_types')): return jsonify({'error':'Permission denied: can_manage_fee_types'}), 403
    elif not (is_super_admin() or get_user_perms(session['user_id']).get('can_manage_masters')): return jsonify({'error':'Permission denied: can_manage_masters'}), 403
    table = master_table(kind)
    if not table: return jsonify({'error':'Invalid master'}), 404
    q(f"DELETE FROM {table} WHERE id=%s", (mid,), commit=True)
    log_action('Delete','Master',mid,kind)
    return jsonify({'success':True})
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
        if r.get('file_path') and is_admin(): sr['file_url'] = f'/uploads/{r["file_path"]}'
        result.append(sr)
    return jsonify(result)

@app.route('/api/documents', methods=['POST'])
@login_required
@require_perm('can_issue_document')
def add_document():
    d = request.json or {}
    row = q_ret("INSERT INTO documents (student_id,student,doc_type,university,issue_date,status,delivered_to,uploaded_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
          (d.get('student_id'),d.get('student'),d.get('doc_type'),d.get('university'),d.get('issue_date') or None,d.get('status','Delivered'),d.get('delivered_to'),session['user_id']))
    log_action('Issue','Document',row['id'] if row else None,d.get('doc_type'))
    return jsonify(serialize(row)), 201

@app.route('/api/documents/<int:did>/upload', methods=['POST'])
@login_required
@require_perm('can_upload_document')
def upload_doc_file(did):
    if 'file' not in request.files: return jsonify({'error':'No file'}), 400
    file = request.files['file']
    file.seek(0, os.SEEK_END); size = file.tell(); file.seek(0)
    if size > MAX_UPLOAD_BYTES: return jsonify({'error':'File size max 512 KB allowed'}), 400
    if not allowed_file(file.filename): return jsonify({'error':'Invalid type'}), 400
    ext = file.filename.rsplit('.',1)[1].lower()
    filename = f"doc_{did}_{uuid.uuid4().hex[:8]}.{ext}"
    file.save(os.path.join(UPLOAD_FOLDER, filename))
    q("UPDATE documents SET file_path=%s,file_name=%s,uploaded_by=%s WHERE id=%s",
      (filename,file.filename,session['user_id'],did), commit=True)
    return jsonify({'success':True,'url':f'/uploads/{filename}'})

@app.route('/api/documents/<int:did>', methods=['DELETE'])
@login_required
@require_perm('can_delete_document')
def delete_document(did):
    doc = q("SELECT file_path FROM documents WHERE id=%s", (did,), one=True)
    if doc and doc.get('file_path'):
        try: os.remove(os.path.join(UPLOAD_FOLDER, doc['file_path']))
        except: pass
    q("DELETE FROM documents WHERE id=%s", (did,), commit=True); log_action('Delete','Document',did); return jsonify({'success':True})

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
@require_perm('can_manage_users')
def get_users():
    if is_super_admin():
        rows = q("SELECT u.id,u.tenant_id,t.name AS tenant_name,u.username,u.full_name,u.role,u.is_active,u.last_login,u.created_at FROM users u LEFT JOIN tenants t ON t.id=u.tenant_id ORDER BY u.id")
    else:
        rows = q("SELECT u.id,u.tenant_id,t.name AS tenant_name,u.username,u.full_name,u.role,u.is_active,u.last_login,u.created_at FROM users u LEFT JOIN tenants t ON t.id=u.tenant_id WHERE u.tenant_id=%s ORDER BY u.id", (current_tenant_id(),))
    result = []
    for r in rows:
        sr = serialize(r); sr['permissions'] = get_user_perms(r['id'])
        univs = q("SELECT u.id,u.name FROM user_universities uu JOIN universities u ON u.id=uu.university_id WHERE uu.user_id=%s", (r['id'],))
        sr['assigned_universities'] = univs
        sr['assigned_university_ids'] = [u['id'] for u in univs]
        result.append(sr)
    return jsonify(result)

@app.route('/api/users', methods=['POST'])
@login_required
@require_perm('can_manage_users')
def add_user():
    d = request.json or {}
    if d.get('role') == 'Super Admin' and not is_super_admin(): return jsonify({'error':'Only Super Admin can create Super Admin'}), 403
    if is_super_admin() and d.get('tenant_name'):
        tenant = q_ret("INSERT INTO tenants (name,status,subscription_start,subscription_end,notes) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (name) DO UPDATE SET status=EXCLUDED.status, subscription_end=EXCLUDED.subscription_end RETURNING id",
                       (d.get('tenant_name').strip(), d.get('tenant_status','Active'), d.get('subscription_start') or date.today().isoformat(), d.get('subscription_end') or None, d.get('tenant_notes')))
        tenant_id = tenant['id']
        src = q("SELECT id FROM tenants WHERE name='Sky Eduworld' LIMIT 1", one=True)
        src_id = src['id'] if src else tenant_id
        q("INSERT INTO academic_sessions (tenant_id,name,start_date,end_date,is_active,created_by) SELECT %s,name,start_date,end_date,is_active,%s FROM academic_sessions WHERE tenant_id=%s ON CONFLICT DO NOTHING", (tenant_id,session['user_id'],src_id), commit=True)
        q("INSERT INTO courses (tenant_id,name,is_active) SELECT %s,name,is_active FROM courses WHERE tenant_id=%s ON CONFLICT DO NOTHING", (tenant_id,src_id), commit=True)
        q("INSERT INTO subjects (tenant_id,name,course_name,is_active) SELECT %s,name,course_name,is_active FROM subjects WHERE tenant_id=%s ON CONFLICT DO NOTHING", (tenant_id,src_id), commit=True)
        q("INSERT INTO fee_types (tenant_id,name,category,is_active) SELECT %s,name,category,is_active FROM fee_types WHERE tenant_id=%s ON CONFLICT DO NOTHING", (tenant_id,src_id), commit=True)
        q("INSERT INTO document_types (tenant_id,name,category,is_active) SELECT %s,name,category,is_active FROM document_types WHERE tenant_id=%s ON CONFLICT DO NOTHING", (tenant_id,src_id), commit=True)
        q("INSERT INTO student_statuses (tenant_id,name,category,is_active) SELECT %s,name,category,is_active FROM student_statuses WHERE tenant_id=%s ON CONFLICT DO NOTHING", (tenant_id,src_id), commit=True)
    else:
        tenant_id = current_tenant_id()
    try:
        new_user = q_ret("INSERT INTO users (tenant_id,username,password,full_name,role) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                         (tenant_id,d.get('username'), hash_pw(d.get('password','')), d.get('full_name'), d.get('role','Staff')))
    except psycopg2.errors.UniqueViolation:
        get_db().rollback(); return jsonify({'error':'Username already exists'}), 409
    uid = new_user['id']; perms = d.get('permissions',{})
    q_ret("""INSERT INTO user_permissions (user_id,
        can_add_student,can_edit_student,can_delete_student,
        can_view_payments,can_add_payment,
        can_view_associates,can_manage_associates,
        can_view_references,can_manage_references,
        can_view_documents,can_upload_document,can_issue_document,can_delete_document,can_manage_masters,
        can_view_student_report,can_view_fee_report,
        can_view_outstanding_report,can_view_assocref_report,can_view_leads_report,
        can_manage_universities,can_view_all_students,
        can_manage_leads,can_view_audit_logs)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
          (uid,
           perms.get('can_add_student',True),perms.get('can_edit_student',True),perms.get('can_delete_student',False),
           perms.get('can_view_payments',True),perms.get('can_add_payment',True),
           perms.get('can_view_associates',False),perms.get('can_manage_associates',False),
           perms.get('can_view_references',False),perms.get('can_manage_references',False),
           perms.get('can_view_documents',True),perms.get('can_upload_document',True),perms.get('can_issue_document',False),perms.get('can_delete_document',False),perms.get('can_manage_masters',False),
           perms.get('can_view_student_report',perms.get('can_view_reports',False)),perms.get('can_view_fee_report',perms.get('can_view_reports',False)),
           perms.get('can_view_outstanding_report',perms.get('can_view_reports',False)),perms.get('can_view_assocref_report',perms.get('can_view_reports',False)),perms.get('can_view_leads_report',perms.get('can_view_reports',False)),
           perms.get('can_manage_universities',False),perms.get('can_view_all_students',False),
           perms.get('can_manage_leads',False),perms.get('can_view_audit_logs',False)))
    q("UPDATE user_permissions SET can_view_accounts=%s, can_manage_accounts=%s, can_view_profit_report=%s, can_manage_users=%s, can_save_partial_student=%s, can_view_fee_types=%s, can_manage_fee_types=%s, can_download_backup=%s WHERE user_id=%s", (perms.get('can_view_accounts',False),perms.get('can_manage_accounts',False),perms.get('can_view_profit_report',False),perms.get('can_manage_users',False),perms.get('can_save_partial_student',False),perms.get('can_view_fee_types',True),perms.get('can_manage_fee_types',False),perms.get('can_download_backup',False),uid), commit=True)
    for univ_id in d.get('assigned_university_ids',[]):
        try: q_ret("INSERT INTO user_universities (user_id,university_id) VALUES (%s,%s) RETURNING id", (uid,univ_id))
        except Exception: get_db().rollback()
    notify_user(uid,'Welcome!',f"Sky Eduworld mein aapka swagat hai, {d.get('full_name')}!",'success')
    log_action('Add','User',uid,d.get('full_name'))
    return jsonify({'success':True,'id':uid}), 201

@app.route('/api/users/<int:uid>', methods=['PUT'])
@login_required
@require_perm('can_manage_users')
def update_user(uid):
    d = request.json or {}
    if d.get('role') == 'Super Admin' and not is_super_admin(): return jsonify({'error':'Only Super Admin can assign Super Admin role'}), 403
    target = q("SELECT tenant_id FROM users WHERE id=%s", (uid,), one=True)
    if not target or not ensure_tenant_access(target.get('tenant_id')): return jsonify({'error':'User access denied'}), 403
    if d.get('full_name') or d.get('role'):
        q("UPDATE users SET full_name=%s,role=%s WHERE id=%s", (d.get('full_name'),d.get('role'),uid), commit=True)
    if d.get('new_password'):
        if len(d['new_password']) < 6: return jsonify({'error':'Min 6 chars'}), 400
        q("UPDATE users SET password=%s WHERE id=%s", (hash_pw(d['new_password']),uid), commit=True)
    if 'permissions' in d:
        perms = d['permissions']
        pv = (
            perms.get('can_add_student',True),perms.get('can_edit_student',True),perms.get('can_delete_student',False),
            perms.get('can_view_payments',True),perms.get('can_add_payment',True),
            perms.get('can_view_associates',False),perms.get('can_manage_associates',False),
            perms.get('can_view_references',False),perms.get('can_manage_references',False),
            perms.get('can_view_documents',True),perms.get('can_upload_document',True),perms.get('can_issue_document',False),perms.get('can_delete_document',False),perms.get('can_manage_masters',False),
            perms.get('can_view_student_report',perms.get('can_view_reports',False)),perms.get('can_view_fee_report',perms.get('can_view_reports',False)),
            perms.get('can_view_outstanding_report',perms.get('can_view_reports',False)),perms.get('can_view_assocref_report',perms.get('can_view_reports',False)),perms.get('can_view_leads_report',perms.get('can_view_reports',False)),
            perms.get('can_manage_universities',False),perms.get('can_view_all_students',False),
            perms.get('can_manage_leads',False),perms.get('can_view_audit_logs',False))
        if q("SELECT id FROM user_permissions WHERE user_id=%s", (uid,), one=True):
            q("""UPDATE user_permissions SET
                can_add_student=%s,can_edit_student=%s,can_delete_student=%s,
                can_view_payments=%s,can_add_payment=%s,
                can_view_associates=%s,can_manage_associates=%s,
                can_view_references=%s,can_manage_references=%s,
                can_view_documents=%s,can_upload_document=%s,can_issue_document=%s,can_delete_document=%s,can_manage_masters=%s,
                can_view_student_report=%s,can_view_fee_report=%s,
                can_view_outstanding_report=%s,can_view_assocref_report=%s,can_view_leads_report=%s,
                can_manage_universities=%s,can_view_all_students=%s,
                can_manage_leads=%s,can_view_audit_logs=%s
                WHERE user_id=%s""", pv+(uid,), commit=True)
        else:
            q_ret("""INSERT INTO user_permissions (user_id,
                can_add_student,can_edit_student,can_delete_student,
                can_view_payments,can_add_payment,
                can_view_associates,can_manage_associates,
                can_view_references,can_manage_references,
                can_view_documents,can_upload_document,can_issue_document,can_delete_document,can_manage_masters,
                can_view_student_report,can_view_fee_report,
                can_view_outstanding_report,can_view_assocref_report,can_view_leads_report,
                can_manage_universities,can_view_all_students,
                can_manage_leads,can_view_audit_logs)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (uid,)+pv)
        q("UPDATE user_permissions SET can_view_accounts=%s, can_manage_accounts=%s, can_view_profit_report=%s, can_manage_users=%s, can_save_partial_student=%s, can_view_fee_types=%s, can_manage_fee_types=%s, can_download_backup=%s WHERE user_id=%s", (perms.get('can_view_accounts',False),perms.get('can_manage_accounts',False),perms.get('can_view_profit_report',False),perms.get('can_manage_users',False),perms.get('can_save_partial_student',False),perms.get('can_view_fee_types',True),perms.get('can_manage_fee_types',False),perms.get('can_download_backup',False),uid), commit=True)
    if 'assigned_university_ids' in d:
        q("DELETE FROM user_universities WHERE user_id=%s", (uid,), commit=True)
        for univ_id in d['assigned_university_ids']:
            try: q_ret("INSERT INTO user_universities (user_id,university_id) VALUES (%s,%s) RETURNING id", (uid,univ_id))
            except Exception: get_db().rollback()
    return jsonify({'success':True})

@app.route('/api/users/<int:uid>/toggle-active', methods=['POST'])
@login_required
@require_perm('can_manage_users')
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
@require_perm('can_manage_users')
def force_logout_user(uid):
    if uid == session.get('user_id'): return jsonify({'error':'Cannot force-logout yourself'}), 400
    q("UPDATE users SET session_token=NULL WHERE id=%s", (uid,), commit=True)
    return jsonify({'success':True})

@app.route('/api/users/<int:uid>', methods=['DELETE'])
@login_required
@require_perm('can_manage_users')
def delete_user(uid):
    if uid == session.get('user_id'): return jsonify({'error':'Cannot delete yourself'}), 400
    q("UPDATE users SET is_active=FALSE, session_token=NULL WHERE id=%s", (uid,), commit=True); log_action('Disable','User',uid); return jsonify({'success':True,'disabled':True})

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
@require_perm('can_view_student_report')
def report_students():
    uid = session['user_id']; fs, fp = student_filter(uid)
    data = q(f"SELECT * FROM students WHERE TRUE {fs} ORDER BY name", fp)
    rows = [[s['id'],s['name'],s['father'],s['mobile'],s['course'],s['university'],s['batch'],s['enroll_no'],s['adm_date'],float(s['total_fee']),float(s['paid']),float(s['total_fee'])-float(s['paid']),s['status']] for s in data]
    return csv_response(rows,['ID','Name','Father','Mobile','Course','University','Batch','Enrollment','Adm Date','Total Fee','Paid','Balance','Status'],f'Students_{datetime.now().strftime("%Y%m%d")}.csv')

@app.route('/api/reports/fees')
@login_required
@require_perm('can_view_fee_report')
def report_fees():
    uid = session['user_id']; fs, fp = student_filter(uid)
    data = q(f"SELECT * FROM students WHERE TRUE {fs} ORDER BY name", fp)
    rows = [[s['name'],s['university'],s['course'],float(s['total_fee']),float(s['paid']),float(s['total_fee'])-float(s['paid']),'Cleared' if float(s['total_fee'])==float(s['paid']) else 'Pending'] for s in data]
    return csv_response(rows,['Student','University','Course','Total Fee','Paid','Balance','Status'],f'Fees_{datetime.now().strftime("%Y%m%d")}.csv')

@app.route('/api/reports/outstanding')
@login_required
@require_perm('can_view_outstanding_report')
def report_outstanding():
    uid = session['user_id']; fs, fp = student_filter(uid)
    data = q(f"SELECT * FROM students WHERE paid < total_fee {fs} ORDER BY (total_fee-paid) DESC", fp)
    rows = [[s['name'],s['mobile'],s['university'],s['course'],float(s['total_fee']),float(s['paid']),float(s['total_fee'])-float(s['paid'])] for s in data]
    return csv_response(rows,['Student','Mobile','University','Course','Total Fee','Paid','Outstanding'],f'Outstanding_{datetime.now().strftime("%Y%m%d")}.csv')

@app.route('/api/reports/assoc-ref')
@login_required
@require_perm('can_view_assocref_report')
def report_assoc_ref():
    uid = session['user_id']
    assocs = q("SELECT * FROM associates ORDER BY pay_date DESC") if is_super_admin() else q("SELECT * FROM associates WHERE created_by=%s ORDER BY pay_date DESC", (uid,))
    refs = q("SELECT * FROM references_ ORDER BY pay_date DESC") if is_super_admin() else q("SELECT * FROM references_ WHERE created_by=%s ORDER BY pay_date DESC", (uid,))
    rows = [['Associate',a['name'],a['phone'],a['student'],a['work_done'],float(a['amount']),a['pay_date'],a['pay_mode'],a['utr'],a['notes']] for a in assocs]
    rows += [['Reference',r['name'],r['phone'],r['student'],'Referral',float(r['amount']),r['pay_date'],r['pay_mode'],r['utr'],r['notes']] for r in refs]
    return csv_response(rows,['Type','Name','Phone','Student','Work','Amount','Date','Mode','UTR','Notes'],f'AssocRef_{datetime.now().strftime("%Y%m%d")}.csv')

@app.route('/api/reports/leads')
@login_required
@require_perm('can_view_leads_report')
def report_leads():
    uid = session['user_id']
    data = q("SELECT * FROM leads ORDER BY created_at DESC") if is_super_admin() else q("SELECT * FROM leads WHERE created_by=%s ORDER BY created_at DESC", (uid,))
    rows = [[r['name'],r['mobile'],r['course'],r['university'],r['source'],r['status'],str(r['created_at'])[:10]] for r in data]
    return csv_response(rows,['Name','Mobile','Course','University','Source','Status','Date'],f'Leads_{datetime.now().strftime("%Y%m%d")}.csv')

# ACCOUNTING / PROFIT
@app.route('/api/accounts/overview')
@login_required
@require_perm('can_view_accounts')
def accounts_overview():
    uid = session['user_id']; fs, fp = student_filter(uid, 's')
    st = q(f"SELECT COALESCE(SUM(s.total_fee),0) AS student_total, COALESCE(SUM(s.paid),0) AS student_received, COALESCE(SUM(s.total_fee-s.paid),0) AS student_due FROM students s WHERE TRUE {fs}", fp, one=True)
    up = q(f"SELECT COALESCE(SUM(up.amount),0) AS payable, COALESCE(SUM(up.paid_amount),0) AS paid FROM university_payables up LEFT JOIN students s ON s.id=up.student_id WHERE TRUE {fs}", fp, one=True)
    ex = q("SELECT COALESCE(SUM(amount),0) AS total FROM expenses", one=True) if is_super_admin() else q("SELECT COALESCE(SUM(amount),0) AS total FROM expenses WHERE created_by=%s", (uid,), one=True)
    assoc = q("SELECT COALESCE(SUM(paid_amount),0) AS total FROM associates", one=True) if is_super_admin() else q("SELECT COALESCE(SUM(paid_amount),0) AS total FROM associates WHERE created_by=%s", (uid,), one=True)
    refs = q("SELECT COALESCE(SUM(paid_amount),0) AS total FROM references_", one=True) if is_super_admin() else q("SELECT COALESCE(SUM(paid_amount),0) AS total FROM references_ WHERE created_by=%s", (uid,), one=True)
    received=float(st['student_received']); total_exp=float(ex['total'])+float(assoc['total'])+float(refs['total'])+float(up['paid'])
    return jsonify({'student_total':float(st['student_total']),'student_received':received,'student_due':float(st['student_due']),'university_payable':float(up['payable']),'university_paid':float(up['paid']),'university_balance':float(up['payable'])-float(up['paid']),'expenses_total':total_exp,'net_profit':received-total_exp})

@app.route('/api/accounts/university-payables', methods=['GET','POST'])
@login_required
def university_payables():
    if request.method == 'POST':
        if not (is_super_admin() or get_user_perms(session['user_id']).get('can_manage_accounts')): return jsonify({'error':'Permission denied: can_manage_accounts'}), 403
        d=request.json or {}; uid=session['user_id']
        sid = d.get('student_id')
        student = q("SELECT id,name,university,univ_fee FROM students WHERE id=%s", (sid,), one=True) if sid else None
        if not student: return jsonify({'error':'Valid existing student select karo'}), 400
        if not q("SELECT id FROM universities WHERE name=%s AND is_active=TRUE", (student['university'],), one=True): return jsonify({'error':'Student ki university master mein active nahi hai'}), 400
        amount = d.get('amount') if d.get('amount') not in (None,'') else student.get('univ_fee',0)
        paid_amount = float(d.get('paid_amount',0) or 0)
        status = 'Paid' if paid_amount >= float(amount or 0) and float(amount or 0)>0 else d.get('status','Pending')
        row=q_ret("INSERT INTO university_payables (student_id,created_by,university,student,amount,paid_amount,fee_type,due_date,paid_date,pay_mode,ref_no,status,remarks) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *", (student['id'],uid,student['university'],student['name'],amount,paid_amount,d.get('fee_type','Tuition'),d.get('due_date') or None,d.get('paid_date') or None,d.get('pay_mode'),d.get('ref_no'),status,d.get('remarks')))
        log_action('Add','University Payable',row['id'] if row else None,d.get('student'))
        return jsonify(serialize(row)),201
    if not (is_super_admin() or get_user_perms(session['user_id']).get('can_view_accounts')): return jsonify({'error':'Permission denied: can_view_accounts'}), 403
    uid=session['user_id']; fs, fp = student_filter(uid, 's')
    rows=q(f"SELECT up.* FROM university_payables up LEFT JOIN students s ON s.id=up.student_id WHERE TRUE {fs} ORDER BY up.created_at DESC", fp)
    return jsonify([serialize(r) for r in rows])

@app.route('/api/expenses', methods=['GET','POST'])
@login_required
def expenses_api():
    if request.method == 'POST':
        if not (is_super_admin() or get_user_perms(session['user_id']).get('can_manage_accounts')): return jsonify({'error':'Permission denied: can_manage_accounts'}), 403
        d=request.json or {}; uid=session['user_id']
        
        try: amount_val=float(d.get('amount',0) or 0)
        except (TypeError, ValueError): amount_val=0
        if not d.get('category') or not d.get('paid_to') or amount_val <= 0: return jsonify({'error':'Category, Paid To aur valid amount zaroori hai'}), 400
        row=q_ret("INSERT INTO expenses (created_by,expense_date,category,amount,pay_mode,paid_to,student,university,associate,reference_name,remarks) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *", (uid,d.get('expense_date') or None,d.get('category','Office'),amount_val,d.get('pay_mode'),d.get('paid_to'),d.get('student'),d.get('university'),d.get('associate'),d.get('reference_name'),d.get('remarks')))
        log_action('Add','Expense',row['id'] if row else None,d.get('category'))
        return jsonify(serialize(row)),201
    if not (is_super_admin() or get_user_perms(session['user_id']).get('can_view_accounts')): return jsonify({'error':'Permission denied: can_view_accounts'}), 403
    uid=session['user_id']
    rows=q("SELECT * FROM expenses ORDER BY expense_date DESC, id DESC") if is_super_admin() else q("SELECT * FROM expenses WHERE created_by=%s ORDER BY expense_date DESC, id DESC", (uid,))
    return jsonify([serialize(r) for r in rows])


@app.route('/api/expenses/<int:eid>', methods=['PUT','DELETE'])
@login_required
def expense_item(eid):
    if not (is_super_admin() or get_user_perms(session['user_id']).get('can_manage_accounts')): return jsonify({'error':'Permission denied: can_manage_accounts'}), 403
    if request.method == 'DELETE':
        q("DELETE FROM expenses WHERE id=%s", (eid,), commit=True); return jsonify({'success':True})
    d=request.json or {}
    try: amount_val=float(d.get('amount',0) or 0)
    except (TypeError, ValueError): amount_val=0
    if not d.get('category') or not d.get('paid_to') or amount_val <= 0: return jsonify({'error':'Category, Paid To aur valid amount zaroori hai'}), 400
    row=q_ret("UPDATE expenses SET expense_date=%s,category=%s,amount=%s,pay_mode=%s,paid_to=%s,student=%s,university=%s,associate=%s,reference_name=%s,remarks=%s WHERE id=%s RETURNING *", (d.get('expense_date') or None,d.get('category'),amount_val,d.get('pay_mode'),d.get('paid_to'),d.get('student'),d.get('university'),d.get('associate'),d.get('reference_name'),d.get('remarks'),eid))
    return jsonify(serialize(row))

@app.route('/api/accounts/university-payables/<int:pid>', methods=['PUT','DELETE'])
@login_required
def university_payable_item(pid):
    if not (is_super_admin() or get_user_perms(session['user_id']).get('can_manage_accounts')): return jsonify({'error':'Permission denied: can_manage_accounts'}), 403
    if request.method == 'DELETE':
        q("DELETE FROM university_payables WHERE id=%s", (pid,), commit=True); return jsonify({'success':True})
    d=request.json or {}
    try: paid=float(d.get('paid_amount',0) or 0); amount=float(d.get('amount',0) or 0)
    except (TypeError, ValueError): return jsonify({'error':'Valid amount zaroori hai'}), 400
    status='Paid' if amount>0 and paid>=amount else d.get('status','Pending')
    row=q_ret("UPDATE university_payables SET amount=%s,paid_amount=%s,fee_type=%s,due_date=%s,paid_date=%s,pay_mode=%s,ref_no=%s,status=%s,remarks=%s WHERE id=%s RETURNING *", (amount,paid,d.get('fee_type','Tuition'),d.get('due_date') or None,d.get('paid_date') or None,d.get('pay_mode'),d.get('ref_no'),status,d.get('remarks'),pid))
    return jsonify(serialize(row))

@app.route('/api/reports/profit')
@login_required
@require_perm('can_view_profit_report')
def report_profit():
    uid=session['user_id']; group=request.args.get('group','month')
    if is_admin():
        fs, fp = student_filter(uid, 's')
    else:
        fs, fp = ' AND s.created_by = %s', [uid]
    params=list(fp); where=f" WHERE TRUE {fs}"
    assoc_filter_vals=[v for v in request.args.get('associates','').split('|') if v]
    ref_filter_vals=[v for v in request.args.get('references','').split('|') if v]
    def add_in(field, key):
        nonlocal where, params
        vals=[v for v in request.args.get(key,'').split('|') if v]
        if vals:
            where += f" AND {field} IN (" + ','.join(['%s']*len(vals)) + ")"
            params.extend(vals)
    add_in('s.name','students'); add_in('s.university','universities'); add_in('s.course','courses'); add_in('s.subject','subjects')
    if assoc_filter_vals:
        where += " AND EXISTS (SELECT 1 FROM associates a2 WHERE LOWER(TRIM(COALESCE(a2.student,'')))=LOWER(TRIM(COALESCE(s.name,''))) AND a2.name IN (" + ','.join(['%s']*len(assoc_filter_vals)) + "))"
        params.extend(assoc_filter_vals)
    if ref_filter_vals:
        where += " AND EXISTS (SELECT 1 FROM references_ r2 WHERE LOWER(TRIM(COALESCE(r2.student,'')))=LOWER(TRIM(COALESCE(s.name,''))) AND r2.name IN (" + ','.join(['%s']*len(ref_filter_vals)) + "))"
        params.extend(ref_filter_vals)
    expr = {'student':'s.name','year':"TO_CHAR(COALESCE(fp.pay_date,fp.created_at::date),'YYYY')"}.get(group, "TO_CHAR(COALESCE(fp.pay_date,fp.created_at::date),'YYYY-MM')")
    if group == 'associate':
        where_assoc = ''
        assoc_params = []
        if assoc_filter_vals:
            where_assoc = "WHERE a.name IN (" + ','.join(['%s']*len(assoc_filter_vals)) + ")"
            assoc_params = assoc_filter_vals
        rows=q(f"""SELECT COALESCE(a.name,'Unassigned') AS bucket, COALESCE(SUM(a.paid_amount),0) AS income, COALESCE(SUM(a.amount),0) AS university_cost FROM associates a {where_assoc} GROUP BY bucket ORDER BY bucket""", assoc_params)
    elif group == 'reference':
        where_ref = ''
        ref_params = []
        if ref_filter_vals:
            where_ref = "WHERE r.name IN (" + ','.join(['%s']*len(ref_filter_vals)) + ")"
            ref_params = ref_filter_vals
        rows=q(f"""SELECT COALESCE(r.name,'Unassigned') AS bucket, COALESCE(SUM(r.paid_amount),0) AS income, COALESCE(SUM(r.amount),0) AS university_cost FROM references_ r {where_ref} GROUP BY bucket ORDER BY bucket""", ref_params)
    else:
        rows=q(f"""WITH pay AS (
                   SELECT fp.student_id, {expr} AS bucket, SUM(fp.amount) AS income
                   FROM fee_payments fp
                   JOIN students s ON s.id=fp.student_id
                   {where}
                   GROUP BY fp.student_id, bucket
                 ),
                 up AS (
                   SELECT student_id, SUM(amount) AS university_cost
                   FROM university_payables
                   GROUP BY student_id
                 )
                 SELECT pay.bucket, COALESCE(SUM(pay.income),0) AS income, COALESCE(SUM(up.university_cost),0) AS university_cost
                 FROM pay
                 LEFT JOIN up ON up.student_id=pay.student_id
                 GROUP BY pay.bucket ORDER BY pay.bucket""", params)
    out=[]
    for r in rows:
        bucket=r['bucket'] or 'Unassigned'; income=float(r['income']); exp=float(r['university_cost'])
        out.append({'bucket':bucket,'income':income,'expense':exp,'profit':income-exp})
    return jsonify(out)

@app.route('/api/reports/staff-business')
@login_required
@admin_required
def report_staff_business():
    rows=q("""SELECT u.id, u.full_name, u.role, COUNT(s.id)::int AS admissions, COALESCE(SUM(s.total_fee),0) AS total_business, COALESCE(SUM(s.paid),0) AS received, COALESCE(SUM(s.total_fee-s.paid),0) AS outstanding, COALESCE(SUM(s.univ_fee),0) AS university_payable FROM users u LEFT JOIN students s ON s.created_by=u.id WHERE u.role <> 'Super Admin' GROUP BY u.id,u.full_name,u.role ORDER BY total_business DESC""")
    return jsonify([serialize(r) for r in rows])

@app.route('/api/backup')
@login_required
@require_perm('can_download_backup')
def download_backup():
    uid=session['user_id']
    scope=request.args.get('scope','mine')
    requested_tenant = request.args.get('tenant_id')
    system = scope == 'system' and is_admin()
    tenant_id = int(requested_tenant) if (requested_tenant and is_super_admin()) else current_tenant_id()
    all_tenants = system and is_super_admin() and not requested_tenant
    data = {'generated_at': datetime.now().isoformat(timespec='seconds'), 'scope': 'system' if system else 'mine', 'user_id': uid, 'tenant_id': None if all_tenants else tenant_id, 'tables': {}}
    def rows(sql, params=()): return [serialize(r) for r in q(sql, params)]
    if system:
        table_names=['users','universities','academic_sessions','courses','subjects','fee_types','document_types','students','fee_payments','fee_installments','university_payables','expenses','associates','references_','documents','leads','follow_ups','activity_logs','login_history']
        for t in table_names:
            try:
                if all_tenants or t in ('activity_logs','login_history'):
                    data['tables'][t]=rows(f"SELECT * FROM {t} ORDER BY id")
                elif t == 'users':
                    data['tables'][t]=rows("SELECT * FROM users WHERE tenant_id=%s ORDER BY id", (tenant_id,))
                else:
                    data['tables'][t]=rows(f"SELECT * FROM {t} WHERE tenant_id=%s ORDER BY id", (tenant_id,))
            except Exception as ex: data['tables'][t]={'error':str(ex)[:120]}
    else:
        data['tables']['students']=rows("SELECT * FROM students WHERE tenant_id=%s ORDER BY id", (tenant_id,))
        data['tables']['fee_payments']=rows("SELECT * FROM fee_payments WHERE tenant_id=%s ORDER BY id", (tenant_id,))
        data['tables']['fee_installments']=rows("SELECT * FROM fee_installments WHERE tenant_id=%s ORDER BY id", (tenant_id,))
        data['tables']['university_payables']=rows("SELECT * FROM university_payables WHERE tenant_id=%s ORDER BY id", (tenant_id,))
        data['tables']['expenses']=rows("SELECT * FROM expenses WHERE tenant_id=%s ORDER BY id", (tenant_id,))
        data['tables']['associates']=rows("SELECT * FROM associates WHERE tenant_id=%s ORDER BY id", (tenant_id,))
        data['tables']['references_']=rows("SELECT * FROM references_ WHERE tenant_id=%s ORDER BY id", (tenant_id,))
        data['tables']['documents']=rows("SELECT * FROM documents WHERE tenant_id=%s ORDER BY id", (tenant_id,))
        data['tables']['leads']=rows("SELECT * FROM leads WHERE tenant_id=%s ORDER BY id", (tenant_id,))
        data['tables']['follow_ups']=rows("SELECT * FROM follow_ups WHERE tenant_id=%s ORDER BY id", (tenant_id,))
    payload=json.dumps(data, ensure_ascii=False, indent=2)
    fname=f"sky_backup_{data['scope']}_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    return Response(payload, mimetype='application/json', headers={'Content-Disposition': f'attachment; filename={fname}'})

@app.route('/api/import/multi', methods=['POST'])
@login_required
def import_multi():
    payload = request.json or {}
    if not isinstance(payload, dict): return jsonify({'error':'Invalid multi import data'}), 400
    uid = session['user_id']
    perms = get_user_perms(uid)
    if not (is_super_admin() or perms.get('can_add_student')): return jsonify({'error':'Permission denied: can_add_student'}), 403

    sheets = {str(k or '').strip().lower(): v for k, v in payload.items() if isinstance(v, list)}
    students = [clean_row(r) for r in (sheets.get('students') or [])]
    fee_structure_rows = [clean_row(r) for r in (sheets.get('student fee structure') or sheets.get('fee structure') or sheets.get('fee installments') or [])]
    fee_rows = [clean_row(r) for r in (sheets.get('student payments') or sheets.get('fee payments') or [])]
    univ_rows = [clean_row(r) for r in (sheets.get('university payments') or sheets.get('university payables') or [])]
    associate_rows = [clean_row(r) for r in (sheets.get('associates') or sheets.get('associate incentives') or [])]
    reference_rows = [clean_row(r) for r in (sheets.get('references') or sheets.get('reference incentives') or [])]

    ensure_student_import_columns()
    active_sess = get_active_session()
    stats = {'students_created':0,'students_updated':0,'fee_structures':0,'student_payments':0,'university_payments':0,'associates':0,'references':0,'duplicates_skipped':0,'cleaned_students':0,'cleaned_related_rows':0}
    errors = []

    def cleanup_all_student_data():
        deleted = 0
        student_count = 0
        try:
            student_count = q("SELECT COUNT(*) AS c FROM students", one=True)['c']
        except Exception:
            student_count = 0
        for sql in [
            "DELETE FROM follow_ups",
            "DELETE FROM documents",
            "DELETE FROM student_photos",
            "DELETE FROM fee_payments",
            "DELETE FROM fee_installments",
            "DELETE FROM university_payables",
            "DELETE FROM expenses",
            "DELETE FROM associates",
            "DELETE FROM references_",
            "DELETE FROM students",
        ]:
            try:
                deleted += q(sql, commit=True) or 0
            except Exception:
                pass
        stats['cleaned_students'] = int(student_count or 0)
        stats['cleaned_related_rows'] = deleted
        return deleted

    def student_payload(row):
        return {
            'external_id': first_val(row,'Student ID','ID','Access ID','Old Student ID','Import Student ID'),
            'name': first_val(row,'Student Name','Name','student_name'),
            'father': first_val(row,'Father Name','Father','father'),
            'mother': first_val(row,'Mother Name','Mother','mother'),
            'dob': parse_date_value(first_val(row,'DOB','Date of Birth')),
            'gender': first_val(row,'Gender'),
            'mobile': first_val(row,'Mobile','Contact No','Contact','Phone'),
            'email': first_val(row,'Email','Mail ID','Mail'),
            'aadhar': first_val(row,'Aadhar','Adhar No','Aadhar No'),
            'address': first_val(row,'Address'),
            'course': first_val(row,'Course','Course Name'),
            'subject': first_val(row,'Subject','Subject Name'),
            'university': first_val(row,'University','University Name'),
            'batch': first_val(row,'Batch','Session'),
            'enroll_no': first_val(row,'Enroll No','University Reg No','Registration No'),
            'roll_no': first_val(row,'Roll No'),
            'adm_date': parse_date_value(first_val(row,'Admission Date','Adm Date','Date of Admission')),
            'remarks': first_val(row,'Remarks','Student Remarks'),
            'total_fee': parse_amount(first_val(row,'Total Fee','Student Decided Fee','Decided Fee for Student','Fee Decided')),
            'univ_fee': parse_amount(first_val(row,'Univ Fee','University Fee','University Decided Fee','Decided for University')),
        }

    def same_text_sql(col):
        return f"LOWER(TRIM(COALESCE({col},'')))=LOWER(TRIM(%s))"

    def is_duplicate_fee_structure(student_id, fee_type, amount):
        return q(f"""SELECT id FROM fee_installments
                    WHERE student_id=%s AND amount=%s AND {same_text_sql('fee_type')}
                    LIMIT 1""", (student_id, amount, fee_type), one=True)

    def is_duplicate_student_payment(student_id, amount, pay_date, pay_mode, fee_type, ref_no, remarks):
        return q(f"""SELECT id FROM fee_payments
                    WHERE student_id=%s AND amount=%s AND pay_date=%s
                      AND {same_text_sql('pay_mode')}
                      AND {same_text_sql('fee_type')}
                      AND {same_text_sql('ref_no')}
                      AND {same_text_sql('remarks')}
                    LIMIT 1""", (student_id, amount, pay_date, pay_mode, fee_type, ref_no, remarks), one=True)

    def is_duplicate_university_payment(student_id, payable, paid, pay_date, pay_mode, fee_type, ref_no, remarks):
        return q(f"""SELECT id FROM university_payables
                    WHERE student_id=%s AND amount=%s AND paid_amount=%s
                      AND COALESCE(paid_date::text,'')=COALESCE(%s,'')
                      AND {same_text_sql('pay_mode')}
                      AND {same_text_sql('fee_type')}
                      AND {same_text_sql('ref_no')}
                      AND {same_text_sql('remarks')}
                    LIMIT 1""", (student_id, payable, paid, pay_date, pay_mode, fee_type, ref_no, remarks), one=True)

    def is_duplicate_associate(name, student_name, work_done, decided):
        return q(f"""SELECT id FROM associates
                    WHERE parent_id IS NULL AND amount=%s
                      AND {same_text_sql('name')}
                      AND {same_text_sql('student')}
                      AND {same_text_sql('work_done')}
                    LIMIT 1""", (decided, name, student_name, work_done), one=True)

    def is_duplicate_reference(name, student_name, university, decided):
        return q(f"""SELECT id FROM references_
                    WHERE parent_id IS NULL AND amount=%s
                      AND {same_text_sql('name')}
                      AND {same_text_sql('student')}
                      AND {same_text_sql('university')}
                    LIMIT 1""", (decided, name, student_name, university), one=True)

    def cleanup_existing_import_rows(student_rows):
        student_ids, student_names = [], []
        for raw in student_rows:
            d = student_payload(raw)
            if d.get('name'):
                student_names.append(d['name'])
            st = None
            if d.get('external_id'):
                sid_text = str(d['external_id']).strip()
                sid_int = sid_text[:-2] if sid_text.endswith('.0') else sid_text
                st = q("SELECT id,name FROM students WHERE external_id IN (%s,%s) ORDER BY id DESC LIMIT 1", (sid_text, sid_int), one=True)
            if not st and d.get('name'):
                imp_key = student_import_key(d)
                st = q("SELECT id,name FROM students WHERE import_key=%s ORDER BY id DESC LIMIT 1", (imp_key,), one=True) if imp_key else None
            if st:
                student_ids.append(st['id'])
                if st.get('name'):
                    student_names.append(st['name'])

        student_ids = list(dict.fromkeys(student_ids))
        student_names = list(dict.fromkeys([n for n in student_names if n]))
        deleted = cleanup_student_related_rows(student_ids, student_names)
        if student_ids:
            q("UPDATE students SET paid=0,total_fee=0 WHERE id = ANY(%s)", (student_ids,), commit=True)
        stats['cleaned_students'] = len(student_ids)
        stats['cleaned_related_rows'] = deleted
        return deleted

    try:
        if payload.get('cleanup_all'):
            cleanup_all_student_data()
            if payload.get('cleanup_only'):
                get_db().commit()
                return jsonify({'success':0, 'stats':stats, 'errors':errors})

        if payload.get('replace_existing') or payload.get('cleanup_only'):
            cleanup_existing_import_rows(students)
            if payload.get('cleanup_only'):
                get_db().commit()
                return jsonify({'success':0, 'stats':stats, 'errors':errors})

        for idx, raw in enumerate(students, start=2):
            try:
                d = student_payload(raw)
                if not d['name']:
                    errors.append({'sheet':'Students','row':idx,'error':'Student Name required'}); continue
                imp_key = student_import_key(d)
                st = q("SELECT * FROM students WHERE import_key=%s ORDER BY id DESC LIMIT 1", (imp_key,), one=True) if imp_key else None
                if not st and imp_key:
                    st = q("""SELECT * FROM students
                            WHERE LOWER(TRIM(COALESCE(name,'')))=LOWER(TRIM(%s))
                              AND LOWER(TRIM(COALESCE(father,'')))=LOWER(TRIM(%s))
                              AND LOWER(TRIM(COALESCE(course,'')))=LOWER(TRIM(%s))
                              AND LOWER(TRIM(COALESCE(subject,'')))=LOWER(TRIM(%s))
                              AND LOWER(TRIM(COALESCE(university,'')))=LOWER(TRIM(%s))
                            ORDER BY id DESC LIMIT 1""",
                           (d['name'], d['father'], d['course'], d['subject'], d['university']), one=True)
                if st:
                    q("""UPDATE students SET external_id=COALESCE(NULLIF(%s,''),external_id),name=%s,father=%s,mother=%s,dob=%s,gender=%s,mobile=%s,email=%s,aadhar=%s,address=%s,course=%s,subject=%s,university=%s,batch=%s,enroll_no=%s,roll_no=%s,adm_date=%s,remarks=%s,total_fee=%s,univ_fee=%s,import_key=%s,status=COALESCE(status,'Active') WHERE id=%s""",
                      (d['external_id'],d['name'],d['father'],d['mother'],d['dob'],d['gender'],d['mobile'],d['email'],d['aadhar'],d['address'],d['course'],d['subject'],d['university'],d['batch'],d['enroll_no'],d['roll_no'],d['adm_date'],d['remarks'],d['total_fee'],d['univ_fee'],imp_key,st['id']), commit=True)
                    stats['students_updated'] += 1
                else:
                    new_student = q_ret("""INSERT INTO students (created_by,session_id,external_id,name,father,mother,dob,gender,mobile,email,aadhar,address,course,subject,university,batch,enroll_no,roll_no,adm_date,remarks,total_fee,paid,univ_fee,import_key,status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s,%s,'Active') RETURNING id""",
                      (uid,active_sess['id'] if active_sess else None,d['external_id'],d['name'],d['father'],d['mother'],d['dob'],d['gender'],d['mobile'],d['email'],d['aadhar'],d['address'],d['course'],d['subject'],d['university'],d['batch'],d['enroll_no'],d['roll_no'],d['adm_date'],d['remarks'],d['total_fee'],d['univ_fee'],imp_key))
                    if new_student: assign_student_code(new_student['id'])
                    stats['students_created'] += 1
            except Exception as row_ex:
                get_db().rollback()
                errors.append({'sheet':'Students','row':idx,'error':str(row_ex)[:180]})
                continue

        for idx, raw in enumerate(fee_structure_rows, start=2):
            try:
                st = find_student_for_import(raw)
                amount = parse_amount(first_val(raw,'Amount','Decided Amount','Fee Amount','Student Decided Fee','Total Fee'))
                fee_type = first_val(raw,'Fee Type','Type') or 'Tuition Fee'
                if not st or amount <= 0:
                    errors.append({'sheet':'Student Fee Structure','row':idx,'error':'Valid existing student and amount required'}); continue
                if is_duplicate_fee_structure(st['id'], fee_type, amount):
                    stats['duplicates_skipped'] += 1
                    continue
                try:
                    q("INSERT INTO fee_types (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (fee_type,), commit=True)
                except Exception:
                    pass
                q_ret("INSERT INTO fee_installments (student_id,created_by,amount,fee_type,due_date,remarks,status) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                      (st['id'],uid,amount,fee_type,parse_date_value(first_val(raw,'Due Date','Date')),first_val(raw,'Remarks','Remark'),'Pending'))
                q("UPDATE students SET total_fee=COALESCE((SELECT SUM(amount) FROM fee_installments WHERE student_id=%s),0) WHERE id=%s", (st['id'],st['id']), commit=True)
                stats['fee_structures'] += 1
            except Exception as ex:
                get_db().rollback()
                errors.append({'sheet':'Student Fee Structure','row':idx,'error':str(ex)[:180]})

        for idx, raw in enumerate(fee_rows, start=2):
            try:
                st = find_student_for_import(raw)
                amount = parse_amount(first_val(raw,'Amount','Received Amount','Payment Amount','Fee Received'))
                if not st or amount <= 0:
                    errors.append({'sheet':'Student Payments','row':idx,'error':'Valid existing student and amount required'}); continue
                fee_type = first_val(raw,'Fee Type','Type') or 'Tuition Fee'
                pay_mode = first_val(raw,'Payment Mode','Mode','Pay Mode') or 'Cash'
                ref_no = first_val(raw,'Ref No / UTR','UTR','Ref No')
                pay_date = parse_date_value(first_val(raw,'Payment Date','Receiving Date','Reciving Date','Date')) or date.today().isoformat()
                remarks = first_val(raw,'Remarks','Remark')
                if is_duplicate_student_payment(st['id'], amount, pay_date, pay_mode, fee_type, ref_no, remarks):
                    stats['duplicates_skipped'] += 1
                    continue
                q_ret("INSERT INTO fee_payments (student_id,recorded_by,amount,fee_type,pay_mode,ref_no,pay_date,remarks,account_bucket) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                      (st['id'],uid,amount,fee_type,pay_mode,ref_no,pay_date,remarks,'student_receivable'))
                q("UPDATE students SET paid=COALESCE((SELECT SUM(amount) FROM fee_payments WHERE student_id=%s),0) WHERE id=%s", (st['id'],st['id']), commit=True)
                stats['student_payments'] += 1
            except Exception as ex:
                get_db().rollback()
                errors.append({'sheet':'Student Payments','row':idx,'error':str(ex)[:180]})

        for idx, raw in enumerate(univ_rows, start=2):
            try:
                st = find_student_for_import(raw)
                payable = parse_amount(first_val(raw,'Payable Amount','University Payable','Decided for University','University Decided Fee','Univ Fee'))
                paid = parse_amount(first_val(raw,'Paid Amount','University Paid','Paid'))
                if not st or (payable <= 0 and paid <= 0):
                    errors.append({'sheet':'University Payments','row':idx,'error':'Valid existing student and payable/paid amount required'}); continue
                if payable < 0: payable = 0
                status = 'Paid' if payable > 0 and paid >= payable else 'Pending'
                fee_type = first_val(raw,'Fee Type','University Fee Type') or 'Tuition'
                pay_date = parse_date_value(first_val(raw,'Payment Date','Paid Date'))
                pay_mode = first_val(raw,'Payment Mode','Mode','Pay Mode')
                ref_no = first_val(raw,'Ref No / UTR','UTR','Ref No')
                remarks = first_val(raw,'Remarks','Remark')
                if is_duplicate_university_payment(st['id'], payable, paid, pay_date, pay_mode, fee_type, ref_no, remarks):
                    stats['duplicates_skipped'] += 1
                    continue
                q_ret("INSERT INTO university_payables (student_id,created_by,university,student,amount,paid_amount,fee_type,due_date,paid_date,pay_mode,ref_no,status,remarks) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                      (st['id'],uid,st.get('university'),st.get('name'),payable,paid,fee_type,parse_date_value(first_val(raw,'Due Date')),pay_date,pay_mode,ref_no,status,remarks))
                stats['university_payments'] += 1
            except Exception as ex:
                get_db().rollback()
                errors.append({'sheet':'University Payments','row':idx,'error':str(ex)[:180]})

        for idx, raw in enumerate(associate_rows, start=2):
            try:
                name = first_val(raw,'Associate Name','Name')
                decided = parse_amount(first_val(raw,'Decided Amount','Amount','Associate Amount'))
                paid = parse_amount(first_val(raw,'Paid Amount','Paid'))
                student_name = first_val(raw,'Student Name','Student')
                if not name or decided <= 0:
                    errors.append({'sheet':'Associates','row':idx,'error':'Associate name and decided amount required'}); continue
                work_done = first_val(raw,'Work Done','Work') or 'Admission'
                if is_duplicate_associate(name, student_name, work_done, decided):
                    stats['duplicates_skipped'] += 1
                    continue
                parent = q_ret("INSERT INTO associates (created_by,name,phone,student,work_done,amount,paid_amount,pay_date,pay_mode,utr,status,notes) VALUES (%s,%s,%s,%s,%s,%s,0,%s,%s,%s,%s,%s) RETURNING id",
                      (uid,name,first_val(raw,'Phone','Mobile'),student_name,work_done,decided,parse_date_value(first_val(raw,'Decided Date','Date')),first_val(raw,'Payment Mode','Mode') or 'Cash',first_val(raw,'UTR','Ref No / UTR'), 'Pending', first_val(raw,'Remarks','Notes')))
                if paid > 0 and parent:
                    q_ret("INSERT INTO associates (created_by,parent_id,name,phone,student,work_done,amount,paid_amount,pay_date,pay_mode,utr,status,notes) VALUES (%s,%s,%s,%s,%s,%s,0,%s,%s,%s,%s,%s,%s) RETURNING id",
                      (uid,parent['id'],name,first_val(raw,'Phone','Mobile'),student_name,work_done,paid,parse_date_value(first_val(raw,'Payment Date','Paid Date')),first_val(raw,'Payment Mode','Mode') or 'Cash',first_val(raw,'UTR','Ref No / UTR'),'Paid',first_val(raw,'Remarks','Notes')))
                    q("UPDATE associates SET paid_amount=%s,status=%s WHERE id=%s", (paid,'Paid' if paid>=decided else 'Partial',parent['id']), commit=True)
                stats['associates'] += 1
            except Exception as ex:
                get_db().rollback()
                errors.append({'sheet':'Associates','row':idx,'error':str(ex)[:180]})

        for idx, raw in enumerate(reference_rows, start=2):
            try:
                name = first_val(raw,'Reference Name','Reference','Name')
                decided = parse_amount(first_val(raw,'Decided Amount','Amount','Reference Amount'))
                paid = parse_amount(first_val(raw,'Paid Amount','Paid'))
                student_name = first_val(raw,'Student Name','Student')
                if not name or not student_name:
                    errors.append({'sheet':'References','row':idx,'error':'Reference name and student required'}); continue
                university = first_val(raw,'University')
                if is_duplicate_reference(name, student_name, university, decided):
                    stats['duplicates_skipped'] += 1
                    continue
                parent = q_ret("INSERT INTO references_ (created_by,name,phone,student,university,amount,paid_amount,pay_date,pay_mode,utr,status,notes) VALUES (%s,%s,%s,%s,%s,%s,0,%s,%s,%s,%s,%s) RETURNING id",
                      (uid,name,first_val(raw,'Phone','Mobile'),student_name,university,decided,parse_date_value(first_val(raw,'Decided Date','Date')),first_val(raw,'Payment Mode','Mode') or 'Cash',first_val(raw,'UTR','Ref No / UTR'),'Pending',first_val(raw,'Remarks','Notes')))
                if paid > 0 and parent:
                    q_ret("INSERT INTO references_ (created_by,parent_id,name,phone,student,university,amount,paid_amount,pay_date,pay_mode,utr,status,notes) VALUES (%s,%s,%s,%s,%s,%s,0,%s,%s,%s,%s,%s,%s) RETURNING id",
                      (uid,parent['id'],name,first_val(raw,'Phone','Mobile'),student_name,university,paid,parse_date_value(first_val(raw,'Payment Date','Paid Date')),first_val(raw,'Payment Mode','Mode') or 'Cash',first_val(raw,'UTR','Ref No / UTR'),'Paid',first_val(raw,'Remarks','Notes')))
                    q("UPDATE references_ SET paid_amount=%s,status=%s WHERE id=%s", (paid,'Paid' if paid>=decided else 'Partial',parent['id']), commit=True)
                stats['references'] += 1
            except Exception as ex:
                get_db().rollback()
                errors.append({'sheet':'References','row':idx,'error':str(ex)[:180]})

        get_db().commit()
    except Exception as ex:
        get_db().rollback()
        return jsonify({'error':str(ex)[:220], 'stats':stats, 'errors':errors}), 500

    log_action('Import','Multi',None,json.dumps(stats))
    return jsonify({'success':sum(stats.values()), 'stats':stats, 'errors':errors})

@app.route('/api/import/students', methods=['POST'])
@login_required
@require_perm('can_add_student')
def import_students():
    rows=request.json or []
    if not isinstance(rows,list): return jsonify({'error':'Invalid import data'}),400
    uid=session['user_id']; ok=0; errors=[]
    for i,d in enumerate(rows, start=1):
        try:

            # Row-wise payment import: copied fee receiving detail sheets usually have one payment per row.
            row_name = d.get('name') or d.get('student_name') or d.get('student')
            row_amount = parse_amount(d.get('amount') or d.get('payment_amount') or d.get('received_amount') or d.get('fee_received'))
            row_date = parse_date_value(d.get('payment_date') or d.get('date') or d.get('pay_date'))
            if row_name and row_amount > 0 and (not d.get('father') or not d.get('mobile')):
                st = q("SELECT * FROM students WHERE LOWER(name)=LOWER(%s) ORDER BY id DESC LIMIT 1", (row_name,), one=True)
                if not st:
                    errors.append({'row':i,'error':f'Student not found for payment row: {row_name}'}); continue
                q_ret("INSERT INTO fee_payments (student_id,recorded_by,amount,fee_type,pay_mode,ref_no,pay_date,remarks,account_bucket) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id", (st['id'],uid,row_amount,d.get('fee_type') or 'Imported Payment',d.get('payment_mode') or d.get('mode') or d.get('pay_mode') or 'Cash',d.get('ref_no') or d.get('utr') or d.get('utr_no') or '',row_date or date.today().isoformat(),d.get('remarks') or d.get('remark') or '', 'student_receivable'))
                q("UPDATE students SET paid=COALESCE(paid,0)+%s WHERE id=%s", (row_amount,st['id']), commit=True)
                univ_paid_row=parse_amount(d.get('university_paid') or d.get('univ_paid'))
                if univ_paid_row>0:
                    q_ret("INSERT INTO university_payables (student_id,created_by,university,student,amount,paid_amount,fee_type,paid_date,pay_mode,ref_no,status,remarks) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id", (st['id'],uid,st.get('university'),st.get('name'),parse_amount(d.get('university_payable') or d.get('univ_fee') or st.get('univ_fee')),univ_paid_row,d.get('university_fee_type') or 'Tuition',row_date,d.get('payment_mode') or d.get('mode') or 'Cash',d.get('ref_no') or d.get('utr') or '', 'Pending', d.get('university_remarks') or d.get('remarks') or 'Imported'))
                ok+=1
                continue
            if not d.get('name'):
                errors.append({'row':i,'error':'Required fields missing'}); continue
            student_total=parse_amount(d.get('total_fee'))
            legacy_paid=parse_amount(d.get('paid'))
            univ_fee=parse_amount(d.get('univ_fee'))
            row=q_ret("""INSERT INTO students (created_by,session_id,name,father,mother,dob,gender,mobile,email,aadhar,address,course,subject,university,batch,enroll_no,roll_no,adm_date,remarks,total_fee,paid,univ_fee,pay_mode,utr,doc_notes,status) VALUES (%s,(SELECT id FROM academic_sessions WHERE is_active=TRUE LIMIT 1),%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""", (uid,d.get('name'),d.get('father'),d.get('mother'),parse_date_value(d.get('dob')),d.get('gender'),d.get('mobile'),d.get('email'),d.get('aadhar'),d.get('address'),d.get('course'),d.get('subject'),d.get('university'),d.get('batch'),d.get('enroll_no'),d.get('roll_no'),parse_date_value(d.get('adm_date')),d.get('remarks') or d.get('student_remarks'),student_total,legacy_paid,univ_fee,d.get('pay_mode'),d.get('utr'),d.get('doc_notes'),'Active'))
            sid=row['id']
            assign_student_code(sid)
            student_payments=collect_numbered_payments(d,'pay',5,d.get('fee_type') or 'Initial Payment',d.get('pay_mode') or 'Cash') or parse_payment_entries(d.get('student_payments') or d.get('fee_payments'), d.get('fee_type') or 'Initial Payment', d.get('pay_mode') or 'Cash')
            if not student_payments and legacy_paid>0:
                student_payments=[{'date':parse_date_value(d.get('payment_date')) or date.today().isoformat(),'fee_type':d.get('fee_type') or 'Initial Payment','amount':legacy_paid,'mode':d.get('pay_mode') or 'Cash','ref':d.get('utr') or '','remarks':d.get('student_fee_remarks') or d.get('remarks') or 'Imported'}]
            paid_total=0
            for pay in student_payments:
                paid_total += pay['amount']
                q_ret("INSERT INTO fee_payments (student_id,recorded_by,amount,fee_type,pay_mode,ref_no,pay_date,remarks,account_bucket) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id", (sid,uid,pay['amount'],pay['fee_type'],pay['mode'],pay['ref'],pay['date'] or date.today().isoformat(),pay['remarks'],'student_receivable'))
            if paid_total and paid_total != legacy_paid:
                q("UPDATE students SET paid=%s WHERE id=%s", (paid_total,sid), commit=True)
            univ_entries=collect_numbered_university_payments(d,'univ_pay',5,d.get('university_fee_type') or 'Tuition',d.get('univ_pay_mode') or d.get('pay_mode') or 'Cash') or parse_university_entries(d.get('university_payments') or d.get('univ_payments'), d.get('university_fee_type') or 'Tuition', d.get('univ_pay_mode') or d.get('pay_mode') or 'Cash')
            legacy_univ_paid=parse_amount(d.get('univ_paid'))
            if not univ_entries and (univ_fee>0 or legacy_univ_paid>0):
                univ_entries=[{'date':parse_date_value(d.get('univ_payment_date')),'fee_type':d.get('university_fee_type') or 'Tuition','payable':univ_fee,'paid':legacy_univ_paid,'mode':d.get('univ_pay_mode') or d.get('pay_mode') or 'Cash','ref':d.get('univ_ref') or '','remarks':d.get('university_remarks') or 'Imported from student sheet'}]
            for up in univ_entries:
                status='Paid' if up['payable']>0 and up['paid']>=up['payable'] else 'Pending'
                q_ret("INSERT INTO university_payables (student_id,created_by,university,student,amount,paid_amount,fee_type,paid_date,pay_mode,ref_no,status,remarks) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id", (sid,uid,d.get('university'),d.get('name'),up['payable'] or univ_fee,up['paid'],up['fee_type'],up['date'],up['mode'],up['ref'],status,up['remarks']))
            assoc_amt=parse_amount(d.get('associate_amount'))
            if d.get('associate_name') and assoc_amt>0:
                q_ret("INSERT INTO associates (created_by,name,phone,student,work_done,amount,pay_date,pay_mode,utr,status,notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id", (uid,d.get('associate_name'),d.get('associate_phone'),d.get('name'),d.get('associate_work') or 'Admission',assoc_amt,parse_date_value(d.get('associate_pay_date')),d.get('associate_pay_mode') or 'Cash',d.get('associate_utr') or '',d.get('associate_status') or 'Paid',d.get('associate_remarks') or 'Imported'))
            ref_amt=parse_amount(d.get('reference_amount'))
            if d.get('reference_name') and ref_amt>0:
                q_ret("INSERT INTO references_ (created_by,name,phone,student,university,amount,pay_date,pay_mode,utr,status,notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id", (uid,d.get('reference_name'),d.get('reference_phone'),d.get('name'),d.get('university'),ref_amt,parse_date_value(d.get('reference_pay_date')),d.get('reference_pay_mode') or 'Cash',d.get('reference_utr') or '',d.get('reference_status') or 'Paid',d.get('reference_remarks') or 'Imported'))
            ok+=1
        except Exception as ex:
            get_db().rollback(); errors.append({'row':i,'error':str(ex)[:180]})
    get_db().commit(); log_action('Import','Students',None,f'{ok} rows')
    return jsonify({'success':ok,'errors':errors})

if __name__ == '__main__':
    port = int(os.environ.get('PORT',5000))
    print(f"\n{'='*50}\n  Sky Eduworld Phase 2\n  URL: http://localhost:{port}\n  Login: admin / sky@2024\n{'='*50}\n")
    app.run(host='0.0.0.0', port=port, debug=os.environ.get('FLASK_ENV')=='development')






























