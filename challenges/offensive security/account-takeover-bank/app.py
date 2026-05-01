from fastapi import FastAPI, HTTPException, Request, Cookie
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import json
import uuid
from datetime import datetime, timedelta

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory database
users = {
    "alice": {
        "password": "alice123",
        "balance": 5000.00,
        "account_number": "1001",
        "username": "alice"
    },
    "bob": {
        "password": "bob456",
        "balance": 10000.00,
        "account_number": "1002",
        "username": "bob"
    },
    "charlie": {
        "password": "charlie789",
        "balance": 2500.00,
        "account_number": "1003",
        "username": "charlie"
    }
}

# Sessions (vulnerable to CSRF and session hijacking)
sessions = {}

# Transaction logs
transactions = []

FLAG = "FLAG{Account_Takeover_Money_Transfer_Success}"

@app.get("/", response_class=HTMLResponse)
async def get_home():
    """Serve the home/introduction page"""
    with open("templates/intro.html", "r") as f:
        return f.read()

@app.get("/login", response_class=HTMLResponse)
async def get_login():
    """Serve the login page"""
    with open("templates/login.html", "r") as f:
        return f.read()

@app.post("/api/login")
async def login(request: Request):
    """
    VULNERABLE LOGIN ENDPOINT
    VULNERABILITIES:
    1. SQL Injection in username field
    2. Weak session tokens
    3. No CSRF protection
    4. Broken authorization
    """
    try:
        data = await request.json()
        username = data.get("username", "")
        password = data.get("password", "")
        
        # VULNERABLE: Simulating SQL query concatenation (SQL Injection)
        # In real code: query = f"SELECT * FROM users WHERE username='{username}' AND password='{password}'"
        # This allows SQL injection through the username parameter
        
        # Check for SQL injection attempts
        if "' --" in username or "' #" in username or "' or '1'='1" in username or "1'='1" in username:
            # Attacker has successfully exploited SQL injection!
            # In a real scenario, the comment removes the password check
            session_id = str(uuid.uuid4())
            sessions[session_id] = {
                "username": "admin",  # Hijacked as admin
                "created_at": datetime.now(),
                "expires_at": datetime.now() + timedelta(hours=24),
                "ip": request.client.host if request.client else "unknown"
            }
            
            return JSONResponse({
                "success": True,
                "message": "SQL Injection successful! You bypassed authentication!",
                "session_id": session_id,
                "username": "admin",
                "vulnerability": "SQL Injection - Authentication Bypass",
                "flag": "FLAG{SQL_Injection_Money_Transfer_Combined_Attack}"
            })
        
        # Normal authentication
        username_lower = username.lower()
        if username_lower in users and users[username_lower]["password"] == password:
            # Create session token
            session_id = str(uuid.uuid4())
            sessions[session_id] = {
                "username": username_lower,
                "created_at": datetime.now(),
                "expires_at": datetime.now() + timedelta(hours=24),
                "ip": request.client.host if request.client else "unknown"
            }
            
            return JSONResponse({
                "success": True,
                "message": f"Login successful! Welcome {username_lower}",
                "session_id": session_id,
                "username": username_lower
            })
        else:
            raise HTTPException(status_code=401, detail="Invalid credentials")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/logout")
async def logout(request: Request):
    """Logout endpoint"""
    session_id = request.headers.get("X-Session-ID")
    if session_id and session_id in sessions:
        del sessions[session_id]
    
    return JSONResponse({"success": True, "message": "Logged out successfully"})

@app.get("/api/account")
async def get_account(request: Request):
    """
    VULNERABLE ENDPOINT
    - No proper session verification
    - Only checks if session exists, doesn't validate ownership
    """
    session_id = request.headers.get("X-Session-ID")
    username_param = request.query_params.get("username", "")
    
    # Vulnerability: Can access any account without proper authorization
    if not username_param:
        if session_id not in sessions:
            raise HTTPException(status_code=401, detail="Not authenticated")
        username = sessions[session_id]["username"]
    else:
        # VULNERABLE: No authorization check - can view any account
        username = username_param.lower()
    
    if username not in users:
        raise HTTPException(status_code=404, detail="User not found")
    
    user = users[username]
    return JSONResponse({
        "username": username,
        "account_number": user["account_number"],
        "balance": user["balance"]
    })

@app.get("/api/users")
async def list_users(request: Request):
    """
    VULNERABLE ENDPOINT
    - Lists all users without authentication
    """
    return JSONResponse({
        "users": [
            {
                "username": username,
                "account_number": users[username]["account_number"],
                "balance": users[username]["balance"]
            }
            for username in users
        ]
    })

@app.post("/api/transfer")
async def transfer_money(request: Request):
    """
    HIGHLY VULNERABLE ENDPOINT
    Exploitation methods:
    1. CSRF Attack - send request from another domain
    2. Session Hijacking - steal session ID
    3. Direct API Manipulation - craft request with any session
    4. No CSRF token verification
    5. No amount validation
    """
    try:
        session_id = request.headers.get("X-Session-ID")
        
        data = await request.json()
        from_account = data.get("from_account", "").lower()
        to_account = data.get("to_account", "").lower()
        amount = float(data.get("amount", 0))
        
        # VULNERABILITY: Weak session check
        # Even if session exists, doesn't verify it belongs to from_account
        if not session_id or session_id not in sessions:
            # MAJOR FLAW: Can still transfer without valid session!
            # Only checking if session exists in ANY form
            pass
        
        # Validate accounts
        if from_account not in users or to_account not in users:
            raise HTTPException(status_code=404, detail="Account not found")
        
        if from_account == to_account:
            raise HTTPException(status_code=400, detail="Cannot transfer to same account")
        
        if amount <= 0:
            raise HTTPException(status_code=400, detail="Amount must be positive")
        
        # Check balance
        if users[from_account]["balance"] < amount:
            raise HTTPException(status_code=400, detail="Insufficient funds")
        
        # VULNERABLE: Actually performs the transfer!
        # No proper authorization check on from_account
        users[from_account]["balance"] -= amount
        users[to_account]["balance"] += amount
        
        # Log transaction
        transaction = {
            "from": from_account,
            "to": to_account,
            "amount": amount,
            "timestamp": datetime.now().isoformat(),
            "session_id": session_id
        }
        transactions.append(transaction)
        
        return JSONResponse({
            "success": True,
            "message": f"Transfer successful! ${amount} sent from {from_account} to {to_account}",
            "flag": FLAG if from_account != to_account and amount > 0 else None,
            "from_balance": users[from_account]["balance"],
            "to_balance": users[to_account]["balance"]
        })
    
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid amount")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/dashboard", response_class=HTMLResponse)
async def get_dashboard():
    """Serve the dashboard"""
    with open("templates/dashboard.html", "r") as f:
        return f.read()

@app.get("/exploit", response_class=HTMLResponse)
async def get_exploit_guide():
    """Serve the exploit guide"""
    with open("templates/exploit.html", "r") as f:
        return f.read()

@app.get("/health")
async def health_check():
    """Health check"""
    return {"status": "ok", "service": "Account Takeover & Money Transfer Lab"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
