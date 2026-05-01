from flask import Flask, render_template, redirect, url_for, request, jsonify
import os

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Mock database with realistic personnel data
users = {
    "9": {
        "username": "alice_w",
        "full_name": "Alice Williams",
        "role": "UX Designer",
        "email": "alice.w@secure-corp.io",
        "bio": "Senior UX Designer. Focused on creating intuitive and accessible infrastructure interfaces.",
        "joined": "January 15, 2024",
        "last_login": "2 hours ago",
        "privacy_token": "UX-992-ALPHA",
        "is_admin": False
    },
    "2": {
        "username": "bob_s",
        "full_name": "Robert Smith",
        "role": "Systems Analyst",
        "email": "robert.smith@secure-corp.io",
        "bio": "Systems Analyst with a passion for optimizing workflows and infrastructure documentation.",
        "joined": "March 22, 2023",
        "last_login": "1 day ago",
        "privacy_token": "SY-441-BETA",
        "is_admin": False
    },
    "30": {
        "username": "admin_master",
        "full_name": "Infrastructure Lead",
        "role": "System Administrator",
        "email": "admin@secure-corp.io",
        "bio": "Managing core infrastructure protocols. Restricted access node. Do not share credentials.",
        "joined": "October 10, 2022",
        "last_login": "Now",
        "privacy_token": "FLAG{IDOR_SYSTEM_BR3ACH_2026}",
        "is_admin": True
    },
    "4": {
        "username": "charlie_d",
        "full_name": "Charlie Davis",
        "role": "DevOps Engineer",
        "email": "charlie.d@secure-corp.io",
        "bio": "DevOps Engineer. Championing automation and continuous deployment across all nodes.",
        "joined": "June 05, 2024",
        "last_login": "5 hours ago",
        "privacy_token": "DO-882-GAMMA",
        "is_admin": False
    }
}

@app.route('/')
def index():
    # Simulate being logged in as Alice (ID 9)
    return redirect(url_for('profile', user_id="9"))

@app.route('/profile/<user_id>')
def profile(user_id):
    user = users.get(user_id)
    if not user:
        return render_template('404.html'), 404
    
    return render_template('profile.html', user=user, user_id=user_id)

@app.route('/api/v1/health')
def health_check():
    return jsonify({
        "status": "operational",
        "version": "2.4.1-stable",
        "node": "SECURE-CORP-IDP-01"
    })

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5004)
