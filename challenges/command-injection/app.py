import subprocess
from flask import Flask, render_template, request

app = Flask(__name__)

@app.route('/', methods=['GET', 'POST'])
def index():
    result = ""
    if request.method == 'POST':
        ip_address = request.form.get('host', '')
        # VULNERABLE: Shell=True and direct injection
        command = f"ping -c 4 {ip_address}"
        try:
            # Note: For Windows compatibility in simulation, we might use different flags, 
            # but usually these simulations target Linux environments.
            output = subprocess.check_output(command, shell=True, stderr=subprocess.STDOUT, text=True)
            result = output
        except Exception as e:
            result = str(e)
            
    return render_template('index.html', result=result)

@app.route('/flag')
def flag():
    # Hard to reach without command injection or direct URL discovery
    return "FLAG{C0MM4ND_INJ3CT10N_W1Z4RD_2026}"

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)
