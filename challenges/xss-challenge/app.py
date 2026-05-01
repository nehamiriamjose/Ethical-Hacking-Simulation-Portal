from flask import Flask, render_template, request, redirect, url_for
import os

app = Flask(__name__)
app.secret_key = os.urandom(24)

# In-memory storage for comments
comments = [
    {"user": "Admin", "text": "Welcome to the guestbook!"},
    {"user": "System", "text": "Please be respectful in your comments."}
]

@app.route('/', methods=['GET'])
def index():
    return render_template('index.html', comments=comments)

@app.route('/comment', methods=['POST'])
def add_comment():
    # Template uses name="comment"
    text = request.form.get('comment', '')
    if text:
        # VULNERABLE: Directly appending user input to the list
        comments.append({"user": "Anonymous", "text": text})
    
    # Check for alert payload to show a hint or flag (simulated)
    if "<script>alert" in text.lower():
        # In a real XSS challenge, the flag would be in a cookie or secret page
        # Here we can just ensure the user knows they succeeded
        pass
        
    return redirect(url_for('index'))

@app.route('/secret-admin-page')
def admin():
    flag = "FLAG{XSS_ST0R3D_VULN_2026}"
    return f"Admin Panel. Flag: {flag}"

if __name__ == '__main__':
    # Use port 5002 as configured in Dockerfile (if any) or standard
    app.run(debug=False, host='0.0.0.0', port=5002)
