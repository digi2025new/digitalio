import os
import psycopg2
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory, make_response
from werkzeug.utils import secure_filename
from pdf2image import convert_from_path  # For PDF conversion
from datetime import datetime, timezone, timedelta  # For scheduling and timezone conversion

app = Flask(__name__)
# Set secret key from environment variable or fallback
app.secret_key = os.getenv('SECRET_KEY', 'your_fallback_secret_key')

# Configuration for file uploads
UPLOAD_FOLDER = 'uploads/'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4', 'mp3', 'pdf', 'docx', 'xlsx'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Function to get a database connection using DATABASE_URL from environment
def get_db_connection():
    DATABASE_URL = os.getenv('DATABASE_URL')
    if not DATABASE_URL:
        raise Exception("DATABASE_URL environment variable not set!")
    return psycopg2.connect(DATABASE_URL)

# Initialize the PostgreSQL database with necessary tables (including scheduled_time)
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

init_db()  # Initialize database on startup

# Helper function to check allowed file extensions
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Home page route
@app.route('/')
def index():
    return render_template('index.html')

# Signup route
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
            resp.set_cookie('signed_up', 'true', max_age=30*24*60*60)  # 30 days
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

# Login route
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
            flash('Login successful.')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid credentials. Try again.')
            return redirect(url_for('login'))
    return render_template('login.html')

# Logout route
@app.route('/logout')
def logout():
    session.pop('username', None)
    session.pop('dept', None)
    flash('Logged out successfully.')
    return redirect(url_for('index'))

# Dashboard route (requires login)
@app.route('/dashboard')
def dashboard():
    if 'username' in session:
        departments = ['extc', 'it', 'mech', 'cs']
        return render_template('dashboard.html', departments=departments)
    else:
        flash('Please login first.')
        return redirect(url_for('login'))

# Department admin login route
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

# Admin panel for uploading and managing notices
@app.route('/admin/<dept>', methods=['GET', 'POST'])
def admin(dept):
    if 'dept' in session and session['dept'] == dept:
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
                                      (dept, image_filename, 'pdf_image', None))
                        conn.commit()
                        flash('PDF converted and images uploaded successfully.')
                    except Exception as e:
                        conn.rollback()
                        flash('Error converting PDF: ' + str(e))
                    finally:
                        conn.close()
                    os.remove(file_path)
                    return redirect(url_for('admin', dept=dept))
                else:
                    c.execute("INSERT INTO notices (department, filename, filetype, scheduled_time) VALUES (%s, %s, %s, %s)",
                              (dept, filename, file_extension, None))
                    conn.commit()
                    conn.close()
                    flash('File uploaded successfully.')
                    return redirect(url_for('admin', dept=dept))
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT * FROM notices WHERE department=%s ORDER BY id DESC", (dept,))
        notices = c.fetchall()
        conn.close()
        return render_template('admin.html', department=dept, notices=notices)
    else:
        flash('Unauthorized access. Please login as the correct department admin.')
        return redirect(url_for('department', dept=dept))

# New route: Scheduling a Notice (with separate date, time, and AM/PM inputs)
@app.route('/schedule_notice/<dept>', methods=['GET', 'POST'])
def schedule_notice(dept):
    # Ensure that only the logged-in admin for the department can schedule notices.
    if 'dept' in session and session['dept'] == dept:
        if request.method == 'POST':
            if 'file' not in request.files:
                flash('No file part.')
                return redirect(request.url)
            file = request.files['file']
            # Retrieve separate date, time, and AM/PM fields from the form
            date_str = request.form.get('date')   # Expected format: YYYY-MM-DD
            time_str = request.form.get('time')   # Expected as hh:mm in 12-hour format
            ampm = request.form.get('ampm')        # "AM" or "PM"
            if file.filename == '' or not (date_str and time_str and ampm):
                flash('Please select a file and scheduled date/time.')
                return redirect(request.url)
            if file and allowed_file(file.filename):
                try:
                    # Combine into a single string, e.g., "2025-03-27 02:30 PM"
                    scheduled_time_str = f"{date_str} {time_str} {ampm}"
                    # Parse using 12-hour format (%I for hour in 12-hour clock)
                    naive_dt = datetime.strptime(scheduled_time_str, "%Y-%m-%d %I:%M %p")
                    # Attach IST timezone (GMT +5:30)
                    ist = timezone(timedelta(hours=5, minutes=30))
                    ist_dt = naive_dt.replace(tzinfo=ist)
                    # Convert IST to UTC for storage
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
        flash('Unauthorized access. Please login as the correct department admin.')
        return redirect(url_for('department', dept=dept))

# Delete a notice
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
            else:
                flash('Unauthorized action.')
        else:
            flash('Notice not found.')
        conn.close()
        return redirect(url_for('admin', dept=session.get('dept')))
    else:
        flash('Unauthorized access.')
        return redirect(url_for('login'))

# Serve uploaded files
@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# Public route: View active notices for a department
@app.route('/<dept>')
def public_dept(dept):
    dept = dept.lower()
    if dept in ['extc', 'it', 'mech', 'cs']:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT * FROM notices WHERE department=%s AND (scheduled_time IS NULL OR scheduled_time <= NOW())", (dept,))
        notices = c.fetchall()
        conn.close()
        return render_template('public.html', department=dept, notices=notices, timer=5)
    else:
        flash('Department not found.')
        return redirect(url_for('index'))

# Slideshow route: Shows only active notices for a department
@app.route('/slideshow/<dept>')
def slideshow(dept):
    dept = dept.lower()
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM notices WHERE department=%s AND (scheduled_time IS NULL OR scheduled_time <= NOW())", (dept,))
    notices = c.fetchall()
    conn.close()
    return render_template('slideshow.html', department=dept, notices=notices, timer=5)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
