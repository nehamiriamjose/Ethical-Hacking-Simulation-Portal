from flask import Flask, render_template, request, redirect, url_for, session
import sqlite3
import os

app = Flask(__name__)
app.secret_key = 'secret_key_123'

# Initialize database
DB_PATH = os.path.join(os.path.dirname(__file__), 'users.db')

def init_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL,
                password TEXT NOT NULL
            )
        ''')
        # Check if users already exist
        cursor.execute("SELECT COUNT(*) FROM users")
        if cursor.fetchone()[0] == 0:
            # Insert sample users
            cursor.execute("INSERT INTO users (username, password) VALUES ('admin', 'admin123')")
            cursor.execute("INSERT INTO users (username, password) VALUES ('user', 'user123')")
            cursor.execute("INSERT INTO users (username, password) VALUES ('jsmith', 'pass123')")
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Database initialization error: {e}")

# Initialize database on startup
init_db()

@app.route('/')
def index():
    message = ""
    sql_query = ""
    return render_template('index.html', message=message, sql_query=sql_query, query_visible=False)

@app.route('/vulnerable-login', methods=['GET', 'POST'])
def vulnerable_login():
    message = ""
    sql_query = ""
    
    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        
        # VULNERABLE CODE - DO NOT USE IN PRODUCTION
        # This demonstrates SQL injection vulnerability
        sql_query = f"SELECT * FROM users WHERE username = '{username}' AND password = '{password}'"
        
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute(sql_query)
            user = cursor.fetchone()
            conn.close()
            
            if user:
                session['username'] = user[1]
                session['logged_in'] = True
                return redirect(url_for('bank_account'))
            else:
                message = "✗ Login Failed - Invalid credentials"
                return render_template('vulnerable_login.html', 
                                     message=message, 
                                     sql_query=sql_query,
                                     query_visible=False,
                                     success=False)
        except Exception as e:
            message = f"✗ Error: {str(e)}"
            return render_template('vulnerable_login.html', 
                                 message=message, 
                                 sql_query=sql_query,
                                 query_visible=False,
                                 success=False)
    
    return render_template('vulnerable_login.html', message=message, sql_query=sql_query, query_visible=False)

@app.route('/bank-account')
def bank_account():
    if not session.get('logged_in'):
        return redirect(url_for('index'))
    
    username = session.get('username', 'User')
    flag = "FLAG{SQL_injection_M4st3r_2026}"
    
    return render_template('bank_account.html', username=username, flag=flag)

@app.route('/secure-login', methods=['GET', 'POST'])
def secure_login():
    message = ""
    sql_query = ""
    
    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        
        # SECURE CODE - Using parameterized queries
        sql_query = "SELECT * FROM users WHERE username = ? AND password = ?"
        
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            # Using parameterized query to prevent SQL injection
            cursor.execute(sql_query, (username, password))
            user = cursor.fetchone()
            conn.close()
            
            if user:
                session['username'] = user[1]
                session['logged_in'] = True
                return redirect(url_for('bank_account'))
            else:
                message = "✗ Login Failed - Invalid credentials"
                return render_template('secure_login.html', 
                                     message=message, 
                                     sql_query=sql_query,
                                     query_visible=False,
                                     success=False)
        except Exception as e:
            message = f"✗ Error: {str(e)}"
            return render_template('secure_login.html', 
                                 message=message, 
                                 sql_query=sql_query,
                                 query_visible=False,
                                 success=False)
    
    return render_template('secure_login.html', message=message, sql_query=sql_query, query_visible=False)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    try:
        app.run(debug=True, host='0.0.0.0', port=5001, use_reloader=False)
    except Exception as e:
        print(f"Startup Error: {e}")
