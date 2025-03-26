import os
import psycopg2
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory, make_response, jsonify
from werkzeug.utils import secure_filename
from pdf2image import convert_from_path
from datetime import datetime, timezone, timedelta

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'your_fallback_secret_key')

UPLOAD_FOLDER = 'uploads/'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4', 'mp3', 'pdf', 'docx', 'xlsx'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def get_db_connection():
    DATABASE_URL = os.getenv('DATABASE_URL')
    if not DATABASE_URL:
        raise Exception("DATABASE_URL environment variable not set!")
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL
                 )''')
    c.execute('''CREATE TABLE IF NOT EXISTS notices (
                    id SERIAL PRIMARY KEY,
                    department TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    filetype TEXT NOT NULL,
                    scheduled_time TIMESTAMP NULL
                 )''')
    conn.commit()
    conn.close()

init_db()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    # ... (your existing signup code)
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    # ... (your existing login code)
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('username', None)
    session.pop('dept', None)
    flash('Logged out successfully.')
    return redirect(url_for('index'))

@app.route('/dashboard')
def dashboard():
    if 'username' in session:
        departments = ['extc', 'it', 'mech', 'cs']
        return render_template('dashboard.html', departments=departments)
    else:
        flash('Please login first.')
        return redirect(url_for('login'))

@app.route('/department/<dept>', methods=['GET', 'POST'])
def department(dept):
    # ... (your existing department admin login code)
    return render_template('department.html', department=dept)

@app.route('/admin/<dept>', methods=['GET', 'POST'])
def admin(dept):
    # ... (your existing admin panel code)
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM notices WHERE department=%s ORDER BY id DESC", (dept,))
    notices = c.fetchall()
    conn.close()
    return render_template('admin.html', department=dept, notices=notices)

@app.route('/schedule_notice/<dept>', methods=['GET', 'POST'])
def schedule_notice(dept):
    # ... (your existing schedule_notice code with IST to UTC conversion)
    if 'dept' in session and session['dept'] == dept:
        if request.method == 'POST':
            if 'file' not in request.files:
                flash('No file part.')
                return redirect(request.url)
            file = request.files['file']
            date_str = request.form.get('date')
            time_str = request.form.get('time')
            ampm = request.form.get('ampm')
            if file.filename == '' or not (date_str and time_str and ampm):
                flash('Please select a file and scheduled date/time.')
                return redirect(request.url)
            if file and allowed_file(file.filename):
                try:
                    scheduled_time_str = f"{date_str} {time_str} {ampm}"
                    naive_dt = datetime.strptime(scheduled_time_str, "%Y-%m-%d %I:%M %p")
                    ist = timezone(timedelta(hours=5, minutes=30))
                    ist_dt = naive_dt.replace(tzinfo=ist)
                    utc_dt = ist_dt.astimezone(timezone.utc)
                except ValueError:
                    flash('Invalid date/time format. Please use the correct format (e.g., 02:30 PM).')
                    return redirect(request.url)
                filename = secure_filename(file.filename)
                file_extension = filename.rsplit('.', 1)[1].lower()
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(file_path)
                conn = get_db_connection()
                c = conn.cursor()
                if file_extension == 'pdf':
                    try:
                        pages = convert_from_path(file_path, dpi=200)
                        base_filename = filename.rsplit('.', 1)[0]
                        for i, page in enumerate(pages, start=1):
                            image_filename = f"{base_filename}_page_{i}.jpg"
                            image_path = os.path.join(app.config['UPLOAD_FOLDER'], image_filename)
                            page.save(image_path, 'JPEG')
                            c.execute("INSERT INTO notices (department, filename, filetype, scheduled_time) VALUES (%s, %s, %s, %s)",
                                      (dept, image_filename, 'pdf_image', utc_dt))
                        conn.commit()
                        flash('PDF converted and images scheduled successfully.')
                    except Exception as e:
                        conn.rollback()
                        flash('Error converting PDF: ' + str(e))
                    finally:
                        conn.close()
                    os.remove(file_path)
                    return redirect(url_for('admin', dept=dept))
                else:
                    c.execute("INSERT INTO notices (department, filename, filetype, scheduled_time) VALUES (%s, %s, %s, %s)",
                              (dept, filename, file_extension, utc_dt))
                    conn.commit()
                    conn.close()
                    flash('Notice scheduled successfully.')
                    return redirect(url_for('admin', dept=dept))
        return render_template('schedule_notice.html', department=dept)
    else:
        flash('Unauthorized access.')
        return redirect(url_for('department', dept=dept))

@app.route('/delete_notice/<int:notice_id>')
def delete_notice(notice_id):
    # ... (your existing delete_notice code)
    return redirect(url_for('admin', dept=session.get('dept')))

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# New JSON endpoint for real-time updates
@app.route('/get_latest_notices/<dept>')
def get_latest_notices(dept):
    dept = dept.lower()
    if dept in ['extc', 'it', 'mech', 'cs']:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("""
            SELECT id, department, filename, filetype, scheduled_time 
            FROM notices 
            WHERE department=%s AND (scheduled_time IS NULL OR scheduled_time <= NOW())
            ORDER BY id DESC
        """, (dept,))
        notices = c.fetchall()
        conn.close()
        results = []
        for n in notices:
            results.append({
                'id': n[0],
                'department': n[1],
                'filename': n[2],
                'filetype': n[3],
                'scheduled_time': n[4].strftime("%Y-%m-%d %H:%M:%S") if n[4] else None
            })
        return jsonify(results)
    else:
        return jsonify([])

@app.route('/<dept>')
def public_dept(dept):
    dept = dept.lower()
    if dept in ['extc', 'it', 'mech', 'cs']:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT * FROM notices WHERE department=%s AND (scheduled_time IS NULL OR scheduled_time <= NOW()) ORDER BY id DESC", (dept,))
        notices = c.fetchall()
        conn.close()
        return render_template('public.html', department=dept, notices=notices, timer=5)
    else:
        flash('Department not found.')
        return redirect(url_for('index'))

@app.route('/slideshow/<dept>')
def slideshow(dept):
    dept = dept.lower()
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM notices WHERE department=%s AND (scheduled_time IS NULL OR scheduled_time <= NOW()) ORDER BY id DESC", (dept,))
    notices = c.fetchall()
    conn.close()
    return render_template('slideshow.html', department=dept, notices=notices, timer=5)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
