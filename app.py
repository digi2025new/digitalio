import os
import psycopg2
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory, make_response
from werkzeug.utils import secure_filename
from pdf2image import convert_from_path  # For PDF conversion
from datetime import datetime, timezone, timedelta

app = Flask(__name__)
# Set secret key from environment variable or fallback
app.secret_key = os.getenv('SECRET_KEY', 'your_fallback_secret_key')

# Configuration for file uploads
UPLOAD_FOLDER = 'uploads/'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4', 'mp3', 'pdf', 'docx', 'xlsx'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def get_db_connection():
    """Get database connection from environment variable."""
    DATABASE_URL = os.getenv('DATABASE_URL')
    if not DATABASE_URL:
        raise Exception("DATABASE_URL environment variable not set!")
    return psycopg2.connect(DATABASE_URL)

def init_db():
    """Initialize tables if they don't exist."""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS notices (
            id SERIAL PRIMARY KEY,
            department TEXT NOT NULL,
            filename TEXT NOT NULL,
            filetype TEXT NOT NULL,
            scheduled_time TIMESTAMP NULL
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def allowed_file(filename):
    """Check if file extension is allowed."""
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

@app.route('/logout')
def logout():
    session.pop('username', None)
    session.pop('dept', None)
    flash('Logged out successfully.')
    return redirect(url_for('index'))

@app.route('/dashboard')
def dashboard():
    """Requires user to be logged in. Shows department choices."""
    if 'username' in session:
        departments = ['extc', 'it', 'mech', 'cs']
        return render_template('dashboard.html', departments=departments)
    else:
        flash('Please login first.')
        return redirect(url_for('login'))

@app.route('/department/<dept>', methods=['GET', 'POST'])
def department(dept):
    """
    Department admin login route.
    Example: for dept 'extc', admin password might be 'extc@22'.
    """
    if request.method == 'POST':
        admin_pass = request.form.get('admin_pass')
        if admin_pass == f"{dept}@22":
            session['dept'] = dept
            flash(f"You are now logged in as {dept.upper()} admin.")
            return redirect(url_for('admin', dept=dept))
        else:
            flash('Incorrect department admin password.')
            return redirect(url_for('department', dept=dept))
    return render_template('department.html', department=dept)

@app.route('/admin/<dept>', methods=['GET', 'POST'])
def admin(dept):
    """
    Admin panel for uploading notices immediately.
    Must be logged in as the correct dept admin.
    """
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
                try:
                    if file_extension == 'pdf':
                        # Convert PDF to images
                        pages = convert_from_path(file_path, dpi=200)
                        base_filename = filename.rsplit('.', 1)[0]
                        for i, page in enumerate(pages, start=1):
                            image_filename = f"{base_filename}_page_{i}.jpg"
                            image_path = os.path.join(app.config['UPLOAD_FOLDER'], image_filename)
                            page.save(image_path, 'JPEG')
                            c.execute("""
                                INSERT INTO notices (department, filename, filetype, scheduled_time)
                                VALUES (%s, %s, %s, %s)
                            """, (dept, image_filename, 'pdf_image', None))
                        conn.commit()
                        flash('PDF converted and images uploaded successfully.')
                    else:
                        c.execute("""
                            INSERT INTO notices (department, filename, filetype, scheduled_time)
                            VALUES (%s, %s, %s, %s)
                        """, (dept, filename, file_extension, None))
                        conn.commit()
                        flash('File uploaded successfully.')
                except Exception as e:
                    conn.rollback()
                    flash(f'Error uploading file: {e}')
                finally:
                    conn.close()
                # Remove original PDF file if we converted it
                if file_extension == 'pdf':
                    os.remove(file_path)
                return redirect(url_for('admin', dept=dept))

        # Show all notices for this dept in descending order
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT * FROM notices WHERE department=%s ORDER BY id DESC", (dept,))
        notices = c.fetchall()
        conn.close()
        return render_template('admin.html', department=dept, notices=notices)
    else:
        flash('Unauthorized access. Please login as the correct department admin.')
        return redirect(url_for('department', dept=dept))

@app.route('/schedule_notice/<dept>', methods=['GET', 'POST'])
def schedule_notice(dept):
    """
    Route for prescheduling notices (IST -> UTC).
    Must be logged in as the correct dept admin.
    """
    if 'dept' in session and session['dept'] == dept:
        if request.method == 'POST':
            if 'file' not in request.files:
                flash('No file part in the request.')
                return redirect(request.url)
            file = request.files['file']
            # Retrieve separate date, time, and AM/PM fields from the form
            date_str = request.form.get('date')   # format: YYYY-MM-DD
            time_str = request.form.get('time')   # format: hh:mm (12-hour)
            ampm = request.form.get('ampm')       # "AM" or "PM"

            if file.filename == '' or not (date_str and time_str and ampm):
                flash('Please select a file and scheduled date/time.')
                return redirect(request.url)

            if file and allowed_file(file.filename):
                # Combine into a single string, e.g., "2025-03-27 02:30 PM"
                scheduled_time_str = f"{date_str} {time_str} {ampm}"
                try:
                    # Parse using 12-hour format
                    naive_dt = datetime.strptime(scheduled_time_str, "%Y-%m-%d %I:%M %p")
                    # Attach IST timezone
                    ist = timezone(timedelta(hours=5, minutes=30))
                    ist_dt = naive_dt.replace(tzinfo=ist)
                    # Convert IST to UTC for storage
                    utc_dt = ist_dt.astimezone(timezone.utc)
                except ValueError:
                    flash('Invalid date/time format. Use hh:mm (e.g., 02:30) and AM/PM.')
                    return redirect(request.url)

                filename = secure_filename(file.filename)
                file_extension = filename.rsplit('.', 1)[1].lower()
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(file_path)
                conn = get_db_connection()
                c = conn.cursor()
                try:
                    if file_extension == 'pdf':
                        pages = convert_from_path(file_path, dpi=200)
                        base_filename = filename.rsplit('.', 1)[0]
                        for i, page in enumerate(pages, start=1):
                            image_filename = f"{base_filename}_page_{i}.jpg"
                            image_path = os.path.join(app.config['UPLOAD_FOLDER'], image_filename)
                            page.save(image_path, 'JPEG')
                            c.execute("""
                                INSERT INTO notices (department, filename, filetype, scheduled_time)
                                VALUES (%s, %s, %s, %s)
                            """, (dept, image_filename, 'pdf_image', utc_dt))
                        conn.commit()
                        flash('PDF converted and images scheduled successfully.')
                        # Remove original PDF
                        os.remove(file_path)
                    else:
                        c.execute("""
                            INSERT INTO notices (department, filename, filetype, scheduled_time)
                            VALUES (%s, %s, %s, %s)
                        """, (dept, filename, file_extension, utc_dt))
                        conn.commit()
                        flash('Notice scheduled successfully.')
                except Exception as e:
                    conn.rollback()
                    flash(f'Error scheduling notice: {e}')
                finally:
                    conn.close()
                return redirect(url_for('admin', dept=dept))
        return render_template('schedule_notice.html', department=dept)
    else:
        flash('Unauthorized access. Please login as the correct department admin.')
        return redirect(url_for('department', dept=dept))

@app.route('/delete_notice/<int:notice_id>')
def delete_notice(notice_id):
    """
    Delete a notice if you are the correct dept admin.
    """
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
                    print(f"Error removing file: {e}")
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

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    """Serve uploaded files from the uploads folder."""
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/<dept>')
def public_dept(dept):
    """
    Public route: shows active notices (immediate or scheduled_time <= NOW())
    for a specific department.
    """
    dept = dept.lower()
    if dept in ['extc', 'it', 'mech', 'cs']:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("""
            SELECT * FROM notices
            WHERE department=%s
              AND (scheduled_time IS NULL OR scheduled_time <= NOW())
        """, (dept,))
        notices = c.fetchall()
        conn.close()
        return render_template('public.html', department=dept, notices=notices, timer=5)
    else:
        flash('Department not found.')
        return redirect(url_for('index'))

@app.route('/slideshow/<dept>')
def slideshow(dept):
    """
    Slideshow route: shows only active notices for the specified department.
    """
    dept = dept.lower()
    if dept in ['extc', 'it', 'mech', 'cs']:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("""
            SELECT * FROM notices
            WHERE department=%s
              AND (scheduled_time IS NULL OR scheduled_time <= NOW())
        """, (dept,))
        notices = c.fetchall()
        conn.close()
        return render_template('slideshow.html', department=dept, notices=notices, timer=5)
    else:
        flash('Department not found.')
        return redirect(url_for('index'))

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
