from flask import Flask, render_template, request
import os

app = Flask(__name__, static_folder='static')

# Directory containing files to be viewed
FILES_DIR = os.path.join(os.path.dirname(__file__), 'pages')

@app.route('/')
def index():
    page = request.args.get('page', 'home.html')
    # SIMULATING VULNERABILITY: In a real LFI, this would be more complex, 
    # but for the simulation, we'll allow path traversal by joining and then reading.
    # Note: os.path.join with an absolute path as the second argument (like /etc/flag.txt) 
    # will discard the first part.
    
    if page.startswith('/'):
        filepath = page
    else:
        filepath = os.path.join(FILES_DIR, page)
    
    try:
        # Check if the user is trying to access the flag
        if 'etc/flag.txt' in page or page == '/etc/flag.txt':
            content = "FLAG{LFI_D0C_EXF1LTRATION_2026}"
        else:
            with open(filepath, 'r') as f:
                content = f.read()
    except Exception as e:
        content = f"Error loading page: {str(e)}"
        
    return render_template('view.html', content=content, page=page)

@app.route('/flag')
def flag_info():
    return "The flag is hidden in /etc/flag.txt inside the container!"

if __name__ == '__main__':
    # Ensure pages directory exists
    if not os.path.exists(FILES_DIR):
        os.makedirs(FILES_DIR)
        with open(os.path.join(FILES_DIR, 'home.html'), 'w') as f:
            f.write("Welcome to our documentation site. Use the menu to navigate.")
        with open(os.path.join(FILES_DIR, 'about.html'), 'w') as f:
            f.write("We are a leading provider of insecure web applications for learning.")
            
    app.run(debug=False, host='0.0.0.0', port=5006)
