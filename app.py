import eventlet
eventlet.monkey_patch()

import os
import psycopg2
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory, make_response, jsonify
from werkzeug.utils import secure_filename
from pdf2image import convert_from_path
from datetime import datetime, timezone, timedelta

# Import SocketIO and related functions
from flask_socketio import SocketIO, join_room, emit

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'your_fallback_secret_key')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)

# Set maximum upload size to 10GB
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024 * 1024  # 10 GB

UPLOAD_FOLDER = 'uploads/'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4', 'mp3', 'pdf', 'docx', 'xlsx'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Initialize SocketIO with Eventlet async mode
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

def get_db_connection():
    DATABASE_URL = os.getenv('DATABASE_URL')
    if not DATABASE_URL:
        raise Exception("DATABASE_URL environment variable not set!")
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    # Create users table if not exists
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    ''')
    # Create notices table if not exists (including expire_time column)
    c.execute('''
        CREATE TABLE IF NOT EXISTS notices (
            id SERIAL PRIMARY KEY,
            department TEXT NOT NULL,
            filename TEXT NOT NULL,
            filetype TEXT NOT NULL,
            scheduled_time TIMESTAMP NULL,
            expire_time TIMESTAMP,
            broadcasted BOOLEAN NOT NULL DEFAULT false
        )
    ''')
    # Ensure expire_time column exists with default value (30 days from NOW)
    c.execute("ALTER TABLE notices ADD COLUMN IF NOT EXISTS expire_time TIMESTAMP NOT NULL DEFAULT (NOW() + INTERVAL '30 days')")
    # Ensure broadcasted column exists
    c.execute("ALTER TABLE notices ADD COLUMN IF NOT EXISTS broadcasted BOOLEAN NOT NULL DEFAULT false")
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
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        conn = get_db_connection()
        c = conn.cursor()
        try:
            c.execute("INSERT INTO users (username, password) VALUES (%s, %s)", (username, password))
            conn.commit()
            resp = make_response(redirect(url_for('login')))
            resp.set_cookie('signed_up', 'true', max_age=30*24*60*60)
            flash('Signup successful. Please login.')
            return resp
        except psycopg2.IntegrityError:
            conn.rollback()
            flash('User already exists. Please login.')
            return redirect(url_for('login'))
        finally:
            conn.close()
    else:
        if request.cookies.get('signed_up'):
            flash('You have already signed up. Please login.')
            return redirect(url_for('login'))
        return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE username=%s AND password=%s", (username, password))
        user = c.fetchone()
        conn.close()
        if user:
            session['username'] = username
            session.permanent = True
            flash('Login successful.')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid credentials. Try again.')
            return redirect(url_for('login'))
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
    if request.method == 'POST':
        admin_pass = request.form.get('admin_pass')
        if admin_pass == f"{dept}@22":
            session['dept'] = dept
            return redirect(url_for('admin', dept=dept))
        else:
            flash('Incorrect department admin password.')
            return redirect(url_for('department', dept=dept))
    return render_template('department.html', department=dept)

############################################
# ADMIN ROUTE: Shows all notices (ignoring expire_time)
############################################
@app.route('/admin/<dept>', methods=['GET', 'POST'])
def admin(dept):
    if 'dept' in session and session['dept'] == dept:
        try:
            if request.method == 'POST' and 'file' in request.files:
                file = request.files['file']
                if file.filename == '':
                    flash('No selected file.')
                    return redirect(request.url)
                if file and allowed_file(file.filename):
                    filename = secure_filename(file.filename)
                    file_extension = filename.rsplit('.', 1)[1].lower()
                    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    file.save(file_path)

                    # Optional expiration date
                    expire_date_str = request.form.get('expire_date')
                    ist = timezone(timedelta(hours=5, minutes=30))
                    if expire_date_str and expire_date_str.strip() != "":
                        try:
                            expire_dt = datetime.strptime(expire_date_str, "%Y-%m-%d")
                            expire_dt = expire_dt.replace(hour=23, minute=59, second=59, tzinfo=ist)
                            expire_time = expire_dt.astimezone(timezone.utc)
                        except Exception:
                            flash("Invalid expiration date format.")
                            return redirect(request.url)
                    else:
                        expire_time = datetime.utcnow().replace(tzinfo=timezone.utc) + timedelta(days=30)

                    conn = get_db_connection()
                    c = conn.cursor()

                    # If it's a PDF, convert to images and insert each page
                    if file_extension == 'pdf':
                        try:
                            pages = convert_from_path(file_path, dpi=200)
                            base_filename = filename.rsplit('.', 1)[0]
                            for i, page in enumerate(pages, start=1):
                                image_filename = f"{base_filename}_page_{i}.jpg"
                                image_path = os.path.join(app.config['UPLOAD_FOLDER'], image_filename)
                                page.save(image_path, 'JPEG')
                                c.execute("""
                                    INSERT INTO notices (department, filename, filetype, scheduled_time, expire_time)
                                    VALUES (%s, %s, %s, %s, %s)
                                    RETURNING id
                                """, (dept, image_filename, 'pdf_image', None, expire_time))
                                new_id = c.fetchone()[0]
                                # Emit new notice event
                                notice_data = {
                                    'id': new_id,
                                    'department': dept,
                                    'filename': image_filename,
                                    'filetype': 'pdf_image',
                                    'scheduled_time': None
                                }
                                socketio.emit('new_notice', notice_data, room=dept)
                            conn.commit()
                            flash('PDF converted and images uploaded successfully.')
                        except Exception as e:
                            conn.rollback()
                            flash('Error converting PDF: ' + str(e))
                        finally:
                            conn.close()
                        os.remove(file_path)
                        return redirect(url_for('admin', dept=dept))

                    # Otherwise, save the file as is (immediate notice)
                    c.execute("""
                        INSERT INTO notices (department, filename, filetype, scheduled_time, expire_time)
                        VALUES (%s, %s, %s, %s, %s)
                        RETURNING id
                    """, (dept, filename, file_extension, None, expire_time))
                    new_id = c.fetchone()[0]
                    conn.commit()
                    conn.close()
                    flash('File uploaded successfully.')

                    # Emit new immediate notice event
                    notice_data = {
                        'id': new_id,
                        'department': dept,
                        'filename': filename,
                        'filetype': file_extension,
                        'scheduled_time': None
                    }
                    socketio.emit('new_notice', notice_data, room=dept)
                    return redirect(url_for('admin', dept=dept))

            # Show immediate notices (ignoring expire_time)
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("""
                SELECT * FROM notices
                WHERE department=%s
                AND (scheduled_time IS NULL OR scheduled_time <= NOW())
                ORDER BY id DESC
            """, (dept,))
            immediate_notices = c.fetchall()

            # Show prescheduled notices (ignoring expire_time)
            c.execute("""
                SELECT * FROM notices
                WHERE department=%s
                AND scheduled_time > NOW()
                ORDER BY id DESC
            """, (dept,))
            prescheduled_notices = c.fetchall()

            conn.close()
            return render_template('admin.html',
                                   department=dept,
                                   immediate_notices=immediate_notices,
                                   prescheduled_notices=prescheduled_notices)
        except Exception as e:
            flash("An unexpected error occurred: " + str(e))
            return redirect(url_for('dashboard'))
    else:
        flash('Unauthorized access. Please enter department admin password.')
        return redirect(url_for('department', dept=dept))

@app.route('/delete_all_notices/<dept>', methods=['POST'])
def delete_all_notices(dept):
    if 'dept' in session and session['dept'] == dept:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT id, filename FROM notices WHERE department=%s", (dept,))
        notices = c.fetchall()
        for (n_id, filename) in notices:
            try:
                os.remove(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            except Exception as e:
                print(e)
            c.execute("DELETE FROM notices WHERE id=%s", (n_id,))
            # Emit delete event for each notice
            socketio.emit('delete_notice', {'id': n_id}, room=dept)
        conn.commit()
        conn.close()
        flash("All notices deleted successfully.")
        return redirect(url_for('admin', dept=dept))
    else:
        flash("Unauthorized access.")
        return redirect(url_for('login'))

@app.route('/schedule_notice/<dept>', methods=['GET', 'POST'])
def schedule_notice(dept):
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
                    ist = timezone(timedelta(hours=5, minutes=30))
                    naive_dt = datetime.strptime(scheduled_time_str, "%Y-%m-%d %I:%M %p")
                    ist_dt = naive_dt.replace(tzinfo=ist)
                    utc_dt = ist_dt.astimezone(timezone.utc)
                except ValueError:
                    flash('Invalid date/time format. Please use the correct format (e.g., 02:30 PM).')
                    return redirect(request.url)

                # Expiration date (optional)
                expire_date_str = request.form.get('expire_date')
                ist = timezone(timedelta(hours=5, minutes=30))
                if expire_date_str and expire_date_str.strip() != "":
                    try:
                        expire_dt = datetime.strptime(expire_date_str, "%Y-%m-%d")
                        expire_dt = expire_dt.replace(hour=23, minute=59, second=59, tzinfo=ist)
                        expire_time = expire_dt.astimezone(timezone.utc)
                    except Exception:
                        flash("Invalid expiration date format.")
                        return redirect(request.url)
                else:
                    expire_time = utc_dt + timedelta(days=30)

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
                            c.execute("""
                                INSERT INTO notices (department, filename, filetype, scheduled_time, expire_time)
                                VALUES (%s, %s, %s, %s, %s)
                            """, (dept, image_filename, 'pdf_image', utc_dt, expire_time))
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
                    c.execute("""
                        INSERT INTO notices (department, filename, filetype, scheduled_time, expire_time)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (dept, filename, file_extension, utc_dt, expire_time))
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
    if 'dept' in session:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT filename, department FROM notices WHERE id=%s", (notice_id,))
        notice = c.fetchone()
        if notice:
            filename, department = notice
            if session['dept'] == department:
                try:
                    os.remove(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                except Exception as e:
                    print(e)
                c.execute("DELETE FROM notices WHERE id=%s", (notice_id,))
                conn.commit()
                flash('Notice deleted successfully.')
                socketio.emit('delete_notice', {'id': notice_id}, room=department)
            else:
                flash('Unauthorized action.')
        else:
            flash('Notice not found.')
        conn.close()
        return redirect(url_for('admin', dept=session.get('dept')))
    else:
        flash('Unauthorized access.')
        return redirect(url_for('login'))

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

###########################################
# PUBLIC DISPLAY: show only valid notices
###########################################
@app.route('/<dept>')
def public_dept(dept):
    dept = dept.lower()
    if dept in ['extc', 'it', 'mech', 'cs']:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("""
            SELECT * FROM notices
            WHERE department=%s
            AND (scheduled_time IS NULL OR scheduled_time <= NOW())
            AND expire_time > NOW()
            ORDER BY id DESC
        """, (dept,))
        notices = c.fetchall()
        conn.close()
        return render_template('slideshow.html', department=dept, notices=notices, hide_nav=True)
    else:
        flash('Department not found.')
        return redirect(url_for('index'))

@app.route('/get_latest_notices/<dept>')
def get_latest_notices(dept):
    dept = dept.lower()
    if dept in ['extc', 'it', 'mech', 'cs']:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("""
            SELECT id, department, filename, filetype, scheduled_time, expire_time
            FROM notices
            WHERE department=%s
            AND (scheduled_time IS NULL OR scheduled_time <= NOW())
            AND expire_time > NOW()
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
                'scheduled_time': n[4].strftime("%Y-%m-%d %H:%M:%S") if n[4] else None,
                'expire_time': n[5].strftime("%Y-%m-%d %H:%M:%S") if n[5] else None
            })
        return jsonify(results)
    else:
        return jsonify([])

# SocketIO event: join a room based on department
@socketio.on('join')
def on_join(dept):
    join_room(dept)
    print(f"A client joined room: {dept}")

# Background task to check scheduled and expired notices
def background_notice_check():
    while True:
        try:
            conn = get_db_connection()
            c = conn.cursor()
            # Check for scheduled notices that are now active and not yet broadcasted
            c.execute("""
                SELECT id, department, filename, filetype, scheduled_time 
                FROM notices
                WHERE scheduled_time IS NOT NULL AND scheduled_time <= NOW() AND broadcasted = false
            """)
            scheduled_notices = c.fetchall()
            for notice in scheduled_notices:
                n_id, dept, filename, filetype, scheduled_time = notice
                notice_data = {
                    'id': n_id,
                    'department': dept,
                    'filename': filename,
                    'filetype': filetype,
                    'scheduled_time': scheduled_time.strftime("%Y-%m-%d %H:%M:%S") if scheduled_time else None
                }
                socketio.emit('new_notice', notice_data, room=dept)
                c.execute("UPDATE notices SET broadcasted = true WHERE id = %s", (n_id,))
            conn.commit()

            # Check for expired notices and remove them
            c.execute("""
                SELECT id, department, filename FROM notices
                WHERE expire_time <= NOW()
            """)
            expired_notices = c.fetchall()
            for notice in expired_notices:
                n_id, dept, filename = notice
                try:
                    os.remove(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                except Exception as e:
                    print(e)
                c.execute("DELETE FROM notices WHERE id = %s", (n_id,))
                socketio.emit('delete_notice', {'id': n_id}, room=dept)
            conn.commit()
            conn.close()
        except Exception as e:
            print("Error in background_notice_check:", e)
        eventlet.sleep(10)

# Start background task
eventlet.spawn(background_notice_check)

if __name__ == '__main__':
    port = int(os.environ.get("PORT") or 5000)
    socketio.run(app, host="0.0.0.0", port=port)
