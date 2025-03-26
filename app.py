import os
import psycopg2
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory, make_response
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
    """Initialize tables if they don't exist and ensure scheduled_time column is present."""
    conn = get_db_connection()
    c = conn.cursor()

    # Create the users table
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    ''')

    # Create the notices table without scheduled_time
    c.execute('''
        CREATE TABLE IF NOT EXISTS notices (
            id SERIAL PRIMARY KEY,
            department TEXT NOT NULL,
            filename TEXT NOT NULL,
            filetype TEXT NOT NULL
        )
    ''')

    # Add scheduled_time column if it doesn't exist
    try:
        c.execute("ALTER TABLE notices ADD COLUMN IF NOT EXISTS scheduled_time TIMESTAMP NULL")
    except Exception as e:
        print(f"Could not add scheduled_time column: {e}")

    conn.commit()
    conn.close()

# Initialize DB on startup
init_db()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    # ...
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    # ...
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
    # ...
    return render_template('department.html', department=dept)

@app.route('/admin/<dept>', methods=['GET', 'POST'])
def admin(dept):
    # ...
    return render_template('admin.html', department=dept, notices=[])

@app.route('/schedule_notice/<dept>', methods=['GET', 'POST'])
def schedule_notice(dept):
    # ...
    return render_template('schedule_notice.html', department=dept)

@app.route('/delete_notice/<int:notice_id>')
def delete_notice(notice_id):
    # ...
    return redirect(url_for('admin', dept=session.get('dept')))

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/<dept>')
def public_dept(dept):
    # ...
    return render_template('public.html', department=dept, notices=[], timer=5)

@app.route('/slideshow/<dept>')
def slideshow(dept):
    # ...
    return render_template('slideshow.html', department=dept, notices=[], timer=5)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
