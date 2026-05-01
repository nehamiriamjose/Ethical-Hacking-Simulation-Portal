from flask import Flask, render_template, request, session, redirect, url_for
import os
from datetime import datetime

app = Flask(__name__)
app.secret_key = "very-secret-key-change-me"

# Simple balance tracker
user_balance = {"admin": 1000, "victim": 5000}

# Transaction history
transactions = []


@app.route('/')
def index():
    if 'user' not in session:
        session['user'] = 'victim'
    return render_template(
        'index.html',
        user=session['user'],
        balance=user_balance.get(session['user'], 0),
        transactions=transactions
    )


@app.route('/transfer', methods=['POST'])
def transfer():
    # VULNERABLE: No CSRF protection
    target = request.form.get('to', '')
    amount_str = request.form.get('amount', '0')
    try:
        amount = int(float(amount_str))
    except ValueError:
        return render_template('transfer_failed.html', error="Invalid amount entered.", user=session.get('user', 'victim'))

    sender = session.get('user', 'victim')
    if not sender or amount <= 0:
        return render_template('transfer_failed.html', error="Invalid transfer parameters.", user=sender)

    if user_balance.get(sender, 0) < amount:
        return render_template('transfer_failed.html', error=f"Insufficient funds. Your balance is ${user_balance.get(sender, 0)}.", user=sender)

    if not target:
        return render_template('transfer_failed.html', error="No recipient specified.", user=sender)

    # Process the transfer
    user_balance[sender] -= amount
    user_balance[target] = user_balance.get(target, 0) + amount

    # Log the transaction
    transactions.insert(0, {
        "sender": sender,
        "recipient": target,
        "amount": amount,
        "timestamp": datetime.now().strftime("%b %d, %Y %I:%M %p"),
        "type": "outgoing"
    })

    return render_template(
        'transfer_success.html',
        sender=sender,
        recipient=target,
        amount=amount,
        new_balance=user_balance[sender],
        user=sender
    )


@app.route('/flag')
def flag():
    admin_balance = user_balance.get('admin', 0)
    if admin_balance > 2000:
        return render_template('flag.html', flag="FLAG{{CSRF_B4NK_HACK_2026}}", admin_balance=admin_balance)
    return render_template(
        'flag.html',
        flag=None,
        admin_balance=admin_balance,
        needed=2001 - admin_balance
    )


@app.route('/attacker')
def attacker():
    return render_template('attacker.html')


@app.route('/reset')
def reset():
    global transactions
    user_balance.clear()
    user_balance.update({"admin": 1000, "victim": 5000})
    transactions = []
    return redirect(url_for('index'))


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))


if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5005)
