from flask import Flask, render_template, request, render_template_string
import os

app = Flask(__name__)

@app.route('/', methods=['GET', 'POST'])
def index():
    name = "Stranger"
    result = ""
    if request.method == 'POST':
        name = request.form.get('name', 'Stranger')
        template = f"Hello {name}, welcome to our secure server!"
        try:
            result = render_template_string(template)
        except Exception as e:
            result = f"Error: {str(e)}"
    
    # Try reading the template manually to see if it exists and can be read
    template_path = os.path.join(app.root_path, 'templates', 'index.html')
    if not os.path.exists(template_path):
        return f"Template missing at {template_path}"
    
    try:
        return render_template('index.html', result=result)
    except Exception as e:
        return f"Template Error: {str(e)}"

@app.route('/flag-location')
def flag_hint():
    return "Flag is stored in the environment variable 'APP_FLAG'"

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5008, use_reloader=False)
