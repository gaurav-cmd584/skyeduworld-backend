"""
student_portal.py — Student self-registration + PHD progress module.

Drop this file next to your app.py (same folder). It plugs into your existing
Flask app as a Blueprint and reuses your existing DB helpers (q, q_ret,
hash_pw, verify_pw, tenant_filter, current_tenant_id, is_admin, get_user_perms,
login_required, super_admin_required, log_action, notify_tenant_admins,
assign_student_code, serialize) directly from app.py — nothing in app.py's
logic is duplicated or forked.

INTEGRATION — add these lines near the top of app.py, AFTER `app = Flask(...)`
and after all the helper functions below it are defined (safest spot: right
before your `@app.route('/')` index route):

    try:
        from student_portal import student_bp
        app.register_blueprint(student_bp)
    except Exception as _sp_ex:
        print('student_portal blueprint not loaded:', _sp_ex)

That's it — two new pages become available:
  /student-portal      -> student self-registration + login + PHD progress
  /referrer-console     -> staff page: referred/unregistered students,
                            PHD stage updates, referral fee entry
                            (uses your EXISTING staff session/login, so a
                            logged-in Admin/Staff user can just open it)

WHAT THIS ADDS
---------------
1. New columns (auto-migrated on first import, safe / idempotent):
     associates.email, references_.email
     students.ref_email, students.referred_type, students.referred_id,
     students.registration_stage, students.student_password,
     students.student_session_token, students.self_registered
2. New table: student_phd_stages (8 standard PhD lifecycle stages per student)
3. Public routes (no staff login needed):
     POST /api/public/student-register   (name, phone, email, ref_email)
     POST /api/public/student-login      (identifier, password)
4. Student-session routes (student must be logged in):
     POST /api/student/logout
     GET  /api/student/me
     POST /api/student/complete-profile
     POST /api/student/change-password
5. Staff routes (existing @login_required from app.py — Admin/Staff session):
     GET  /api/referred-students
     GET  /api/students/<id>/phd-stages
     PUT  /api/students/<id>/phd-stages/<stage_id>
     GET  /api/students/<id>/referral-fee
     POST /api/students/<id>/referral-fee
     GET  /api/unassigned-students          (Super Admin only)
     POST /api/unassigned-students/<id>/assign (Super Admin only)

HOW THE REFERENCE-EMAIL LINK WORKS
-----------------------------------
Student fills Name / Phone / Email / Reference Email on the public form.
Reference Email is matched (case-insensitive) against `associates.email`
first, then `references_.email` (an Associate and a Reference/Consultant
record are treated the same way — you told me Consultant = Reference).
  - match in associates    -> referred_type='associate', referred_id=<id>
  - match in references_   -> referred_type='reference', referred_id=<id>
  - no match anywhere      -> referred_type='unassigned' and the record is
                              queued for Super Admin to manually assign via
                              /referrer-console (Super Admin section) or the
                              /api/unassigned-students endpoints.

The student lands in your existing `students` table with status='Draft'
(same convention your app already uses for partial/unregistered students)
and registration_stage='Unregistered'. After they log in and submit the
full profile form, registration_stage becomes 'Profile Completed'. The FEE
SECTION (total_fee / payments) is only ever touched by:
  - Admin / Super Admin (always), or
  - the specific Associate-manager (can_manage_associates permission) if
    referred_type='associate', or
  - the specific Reference/Consultant-manager (can_manage_references
    permission) if referred_type='reference'.
A student can NEVER see fee data — /api/student/me deliberately returns only
name/mobile/email/course/university/registration stage + PHD progress, never
total_fee/paid/fee_payments.
"""

import re
import uuid
from functools import wraps

from flask import Blueprint, request, jsonify, session, g, send_from_directory

import app as core  # reuses the existing Flask app + all its DB/auth helpers

student_bp = Blueprint('student_portal', __name__)

PHD_STAGES = [
    'Registration',
    'Coursework',
    'RRC / Proposal Approval',
    'Synopsis Submission',
    'Pre-Submission Seminar',
    'Thesis Submission',
    'Viva-Voce',
    'Degree Awarded',
]


# --------------------------------------------------------------------------
# One-time schema migration (idempotent — safe to run on every deploy)
# --------------------------------------------------------------------------
def ensure_schema():
    try:
        core.q("ALTER TABLE associates ADD COLUMN IF NOT EXISTS email TEXT", commit=True)
        core.q("ALTER TABLE references_ ADD COLUMN IF NOT EXISTS email TEXT", commit=True)
        for col_sql in [
            "ADD COLUMN IF NOT EXISTS ref_email TEXT",
            "ADD COLUMN IF NOT EXISTS referred_type TEXT",
            "ADD COLUMN IF NOT EXISTS referred_id INTEGER",
            "ADD COLUMN IF NOT EXISTS registration_stage TEXT DEFAULT 'Registered'",
            "ADD COLUMN IF NOT EXISTS student_password TEXT",
            "ADD COLUMN IF NOT EXISTS student_session_token TEXT",
            "ADD COLUMN IF NOT EXISTS self_registered BOOLEAN DEFAULT FALSE",
        ]:
            core.q(f"ALTER TABLE students {col_sql}", commit=True)
        core.q("""
            CREATE TABLE IF NOT EXISTS student_phd_stages (
                id SERIAL PRIMARY KEY,
                tenant_id INTEGER,
                student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                stage_order INTEGER NOT NULL,
                stage_name TEXT NOT NULL,
                status TEXT DEFAULT 'Pending',
                remarks TEXT,
                updated_by INTEGER REFERENCES users(id),
                updated_at TIMESTAMP DEFAULT NOW(),
                created_at TIMESTAMP DEFAULT NOW()
            )
        """, commit=True)
        core.q("UPDATE students SET registration_stage='Registered' WHERE registration_stage IS NULL", commit=True)
    except Exception as ex:
        try:
            core.get_db().rollback()
        except Exception:
            pass
        print('student_portal schema migration failed:', ex)


with core.app.app_context():
    ensure_schema()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def resolve_tenant_id(d):
    """Multi-tenant aware: tries subdomain, then client_code in the payload,
    then falls back to the default 'Sky Eduworld' tenant (same fallback your
    /api/login route already uses)."""
    host = (request.host or '').split(':')[0].lower()
    parts = host.split('.')
    if len(parts) >= 3:
        t = core.q("SELECT id FROM tenants WHERE LOWER(subdomain)=LOWER(%s)", (parts[0],), one=True)
        if t:
            return t['id']
    client_code = (d.get('client_code') or '').strip()
    if client_code:
        t = core.q("SELECT id FROM tenants WHERE LOWER(client_code)=LOWER(%s)", (client_code,), one=True)
        if t:
            return t['id']
    t = core.q("SELECT id FROM tenants WHERE name='Sky Eduworld' LIMIT 1", one=True)
    return t['id'] if t else None


def find_referrer_by_email(tenant_id, email):
    email = (email or '').strip().lower()
    if not email:
        return 'unassigned', None
    a = core.q("SELECT id FROM associates WHERE tenant_id=%s AND parent_id IS NULL AND LOWER(email)=%s LIMIT 1",
               (tenant_id, email), one=True)
    if a:
        return 'associate', a['id']
    r = core.q("SELECT id FROM references_ WHERE tenant_id=%s AND parent_id IS NULL AND LOWER(email)=%s LIMIT 1",
               (tenant_id, email), one=True)
    if r:
        return 'reference', r['id']
    return 'unassigned', None


def seed_phd_stages(student_id, tenant_id):
    for i, name in enumerate(PHD_STAGES, start=1):
        status = 'Completed' if i == 1 else 'Pending'
        core.q("INSERT INTO student_phd_stages (tenant_id,student_id,stage_order,stage_name,status) "
               "VALUES (%s,%s,%s,%s,%s)", (tenant_id, student_id, i, name, status), commit=True)


def student_public_payload(s):
    return {
        'id': s['id'], 'name': s.get('name'), 'mobile': s.get('mobile'), 'email': s.get('email'),
        'student_code': s.get('student_code'), 'registration_stage': s.get('registration_stage'),
        'course': s.get('course'), 'university': s.get('university'),
    }


def student_login_required(f):
    @wraps(f)
    def wrap(*a, **kw):
        sid = session.get('student_id')
        if not sid or not session.get('is_student'):
            return jsonify({'error': 'Student login required'}), 401
        student = core.q("SELECT * FROM students WHERE id=%s", (sid,), one=True)
        if not student or student.get('student_session_token') != session.get('student_session_token'):
            session.clear()
            return jsonify({'error': 'Session expired, please login again'}), 401
        g.student = student
        return f(*a, **kw)
    return wrap


def can_manage_fee(student):
    """Fee section gate: Admin/Super Admin always; otherwise only the
    permission-holder matching how this student was referred."""
    if core.is_admin():
        return True
    uid = session.get('user_id')
    if not uid:
        return False
    perms = core.get_user_perms(uid)
    rtype = student.get('referred_type')
    if rtype == 'associate':
        return bool(perms.get('can_manage_associates'))
    if rtype == 'reference':
        return bool(perms.get('can_manage_references'))
    return True  # normal (non self-registered) student — leave to existing app perms


# --------------------------------------------------------------------------
# Public: registration + login
# --------------------------------------------------------------------------
@student_bp.route('/api/public/student-register', methods=['POST'])
def public_student_register():
    d = request.json or {}
    ip = request.remote_addr or 'unknown'
    if core._ip_rate_limited(ip):
        return jsonify({'error': 'Bahut zyada attempts. Thodi der baad try karein.'}), 429

    name = (d.get('name') or '').strip()
    phone = re.sub(r'\D', '', str(d.get('phone') or ''))
    email = (d.get('email') or '').strip()
    ref_email = (d.get('ref_email') or '').strip()

    if not name or not phone or not email or not ref_email:
        return jsonify({'error': 'Name, Phone, Email aur Reference Email — sabhi zaroori hain'}), 400
    if len(phone) < 8:
        return jsonify({'error': 'Sahi phone number daaliye'}), 400
    if '@' not in email or '@' not in ref_email:
        return jsonify({'error': 'Sahi email format daaliye'}), 400

    tid = resolve_tenant_id(d)
    if not tid:
        return jsonify({'error': 'Institute record nahi mila'}), 404

    dup = core.q("SELECT id FROM students WHERE tenant_id=%s AND (mobile=%s OR LOWER(email)=LOWER(%s))",
                 (tid, phone, email), one=True)
    if dup:
        return jsonify({'error': 'Is phone ya email se pehle se ek registration maujood hai. Login karein.'}), 400

    referred_type, referred_id = find_referrer_by_email(tid, ref_email)

    row = core.q_ret("""
        INSERT INTO students (tenant_id,name,mobile,email,ref_email,referred_type,referred_id,
                               registration_stage,status,student_password,self_registered)
        VALUES (%s,%s,%s,%s,%s,%s,%s,'Unregistered','Draft',%s,TRUE) RETURNING *
    """, (tid, name, phone, email, ref_email, referred_type, referred_id, core.hash_pw(phone)))

    if not row:
        return jsonify({'error': 'Registration fail ho gayi, dobara try karein'}), 500

    try:
        core.assign_student_code(row['id'])
        seed_phd_stages(row['id'], tid)
        if referred_type == 'unassigned':
            core.notify_tenant_admins(tid, 'New unassigned student registration',
                                       f"{name} ne register kiya, reference email match nahi hui — please assign.",
                                       'warning')
        else:
            core.notify_tenant_admins(tid, 'New student registration',
                                       f"{name} ne register kiya hai ({referred_type} ke through).", 'info')
    except Exception:
        pass

    return jsonify({
        'success': True,
        'message': 'Registration successful. Aapka default password aapka phone number hai — login karke turant badal lein.',
        'student_code': row.get('student_code'),
    }), 201


@student_bp.route('/api/public/student-login', methods=['POST'])
def public_student_login():
    d = request.json or {}
    ip = request.remote_addr or 'unknown'
    if core._ip_rate_limited(ip):
        return jsonify({'error': 'Bahut zyada attempts. Thodi der baad try karein.'}), 429

    identifier = (d.get('identifier') or '').strip()
    password = (d.get('password') or '').strip()
    if not identifier or not password:
        return jsonify({'error': 'Phone/Email aur Password daalein'}), 400

    student = core.q("SELECT * FROM students WHERE mobile=%s OR LOWER(email)=LOWER(%s) LIMIT 1",
                      (identifier, identifier), one=True)
    if not student or not student.get('student_password') or not core.verify_pw(student['student_password'], password):
        return jsonify({'error': 'Invalid login details'}), 401

    token = str(uuid.uuid4())
    core.q("UPDATE students SET student_session_token=%s WHERE id=%s", (token, student['id']), commit=True)

    for k in ('user_id', 'role', 'tenant_id', 'session_token', 'csrf_token'):
        session.pop(k, None)
    session.permanent = True
    session['student_id'] = student['id']
    session['is_student'] = True
    session['student_session_token'] = token

    return jsonify({'success': True, 'student': student_public_payload(student)})


@student_bp.route('/api/student/logout', methods=['POST'])
def student_logout():
    sid = session.get('student_id')
    if sid:
        core.q("UPDATE students SET student_session_token=NULL WHERE id=%s", (sid,), commit=True)
    session.clear()
    return jsonify({'success': True})


# --------------------------------------------------------------------------
# Student-session routes
# --------------------------------------------------------------------------
@student_bp.route('/api/student/me', methods=['GET'])
@student_login_required
def student_me():
    s = g.student
    stages = core.q(
        "SELECT stage_order,stage_name,status,remarks,updated_at FROM student_phd_stages "
        "WHERE student_id=%s ORDER BY stage_order", (s['id'],))
    payload = student_public_payload(s)
    payload['profile_complete'] = s.get('registration_stage') != 'Unregistered'
    payload['phd_progress'] = [core.serialize(x) for x in stages]
    return jsonify(payload)


@student_bp.route('/api/student/complete-profile', methods=['POST'])
@student_login_required
def student_complete_profile():
    s = g.student
    d = request.json or {}
    required = ['father', 'dob', 'gender', 'address', 'course', 'university']
    missing = [r for r in required if not d.get(r)]
    if missing:
        return jsonify({'error': f"Ye fields zaroori hain: {', '.join(missing)}"}), 400
    core.q("""
        UPDATE students SET father=%s, mother=%s, dob=%s, gender=%s, aadhar=%s, address=%s, state=%s,
               district=%s, pincode=%s, course=%s, subject=%s, university=%s, batch=%s, enroll_no=%s,
               roll_no=%s, remarks=%s, registration_stage='Profile Completed'
        WHERE id=%s
    """, (d.get('father'), d.get('mother'), d.get('dob') or None, d.get('gender'), d.get('aadhar'),
          d.get('address'), d.get('state'), d.get('district'), d.get('pincode'), d.get('course'),
          d.get('subject'), d.get('university'), d.get('batch'), d.get('enroll_no'), d.get('roll_no'),
          d.get('remarks'), s['id']), commit=True)
    core.notify_tenant_admins(s['tenant_id'], 'Student profile completed',
                               f"{s['name']} ne apna profile complete kar diya — fee section fill karein.", 'info')
    return jsonify({'success': True})


@student_bp.route('/api/student/change-password', methods=['POST'])
@student_login_required
def student_change_password():
    s = g.student
    d = request.json or {}
    old = (d.get('old_password') or '').strip()
    new = (d.get('new_password') or '').strip()
    if not core.verify_pw(s.get('student_password'), old):
        return jsonify({'error': 'Purana password galat hai'}), 400
    if len(new) < 4:
        return jsonify({'error': 'Naya password kam se kam 4 characters ka ho'}), 400
    core.q("UPDATE students SET student_password=%s WHERE id=%s", (core.hash_pw(new), s['id']), commit=True)
    return jsonify({'success': True})


# --------------------------------------------------------------------------
# Staff-session routes (reuses app.py's existing login_required / permissions)
# --------------------------------------------------------------------------
@student_bp.route('/api/referred-students', methods=['GET'])
@core.login_required
def referred_students():
    tid = core.current_tenant_id()
    uid = session.get('user_id')
    base = "SELECT * FROM students WHERE tenant_id=%s AND self_registered=TRUE"
    params = [tid]
    if not core.is_admin():
        perms = core.get_user_perms(uid)
        clauses = []
        if perms.get('can_manage_associates'):
            clauses.append("referred_type='associate'")
        if perms.get('can_manage_references'):
            clauses.append("referred_type='reference'")
        if not clauses:
            return jsonify([])
        base += " AND (" + " OR ".join(clauses) + ")"
    base += " ORDER BY id DESC"
    rows = core.q(base, params)
    return jsonify([core.serialize(r) for r in rows])


@student_bp.route('/api/students/<int:sid>/phd-stages', methods=['GET'])
@core.login_required
def get_phd_stages(sid):
    tf, tp = core.tenant_filter('', include_super=True)
    student = core.q(f"SELECT * FROM students WHERE id=%s{tf}", (sid, *tp), one=True)
    if not student:
        return jsonify({'error': 'Not found'}), 404
    rows = core.q("SELECT * FROM student_phd_stages WHERE student_id=%s ORDER BY stage_order", (sid,))
    return jsonify([core.serialize(r) for r in rows])


@student_bp.route('/api/students/<int:sid>/phd-stages/<int:stage_id>', methods=['PUT'])
@core.login_required
def update_phd_stage(sid, stage_id):
    tf, tp = core.tenant_filter('', include_super=True)
    student = core.q(f"SELECT * FROM students WHERE id=%s{tf}", (sid, *tp), one=True)
    if not student:
        return jsonify({'error': 'Not found'}), 404
    if not can_manage_fee(student):
        return jsonify({'error': 'Permission denied: sirf Admin ya referring Associate/Reference handler hi update kar sakte hain'}), 403
    d = request.json or {}
    status = d.get('status', 'Pending')
    if status not in ('Pending', 'In Progress', 'Completed'):
        return jsonify({'error': 'Invalid status'}), 400
    core.q("UPDATE student_phd_stages SET status=%s, remarks=%s, updated_by=%s, updated_at=NOW() "
           "WHERE id=%s AND student_id=%s", (status, d.get('remarks'), session.get('user_id'), stage_id, sid), commit=True)
    core.log_action('Update', 'PHD Stage', sid, status)
    return jsonify({'success': True})


@student_bp.route('/api/students/<int:sid>/referral-fee', methods=['GET', 'POST'])
@core.login_required
def referral_fee(sid):
    tf, tp = core.tenant_filter('', include_super=True)
    student = core.q(f"SELECT * FROM students WHERE id=%s{tf}", (sid, *tp), one=True)
    if not student:
        return jsonify({'error': 'Not found'}), 404
    if not can_manage_fee(student):
        return jsonify({'error': 'Permission denied. Sirf Admin ya referring Associate/Reference handler hi fee section access kar sakte hain.'}), 403

    if request.method == 'GET':
        payments = core.q("SELECT * FROM fee_payments WHERE student_id=%s ORDER BY id DESC", (sid,))
        return jsonify({'student': core.serialize(student), 'payments': [core.serialize(p) for p in payments]})

    d = request.json or {}
    uid = session.get('user_id')
    tid = core.current_tenant_id() or student.get('tenant_id')
    amount = float(d.get('amount') or 0)
    if amount > 0:
        core.q_ret("""INSERT INTO fee_payments (tenant_id,student_id,recorded_by,amount,fee_type,pay_mode,ref_no,pay_date,remarks)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                   (tid, sid, uid, amount, d.get('fee_type', 'Tuition Fee'), d.get('pay_mode', 'Cash'),
                    d.get('ref_no'), d.get('pay_date') or None, d.get('remarks')))
    if d.get('total_fee') is not None:
        core.q("UPDATE students SET total_fee=%s WHERE id=%s", (d.get('total_fee'), sid), commit=True)
    if student.get('registration_stage') != 'Active':
        core.q("UPDATE students SET registration_stage='Active', status='Active' WHERE id=%s", (sid,), commit=True)
    core.log_action('Add', 'Referral Fee', sid, str(amount))
    return jsonify({'success': True})


@student_bp.route('/api/unassigned-students', methods=['GET'])
@core.super_admin_required
def unassigned_students():
    rows = core.q("""SELECT s.*, t.name AS tenant_name FROM students s JOIN tenants t ON t.id=s.tenant_id
                      WHERE s.referred_type='unassigned' ORDER BY s.id DESC""")
    return jsonify([core.serialize(r) for r in rows])


@student_bp.route('/api/unassigned-students/<int:sid>/assign', methods=['POST'])
@core.super_admin_required
def assign_unassigned(sid):
    d = request.json or {}
    rtype = d.get('referred_type')
    rid = d.get('referred_id')
    if rtype not in ('associate', 'reference') or not rid:
        return jsonify({'error': "referred_type ('associate'/'reference') and referred_id required"}), 400
    core.q("UPDATE students SET referred_type=%s, referred_id=%s WHERE id=%s", (rtype, rid, sid), commit=True)
    core.log_action('Assign', 'Unassigned Student', sid, f'{rtype}:{rid}')
    return jsonify({'success': True})


# --------------------------------------------------------------------------
# Static pages
# --------------------------------------------------------------------------
@student_bp.route('/student-portal')
def student_portal_page():
    return send_from_directory(core.app.static_folder, 'student_portal.html')


@student_bp.route('/referrer-console')
def referrer_console_page():
    return send_from_directory(core.app.static_folder, 'referrer_console.html')
