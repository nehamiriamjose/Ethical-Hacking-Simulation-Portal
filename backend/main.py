import os
import sqlite3
import markdown
import time
import uuid
import httpx
from pathlib import Path
from fastapi import FastAPI, Form, Body, HTTPException, UploadFile, File, Request
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from datetime import datetime
import sys
import logging
from urllib.parse import quote
sys.path.insert(0, os.path.dirname(__file__))

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    from docker_manager import DockerManager, DOCKER_AVAILABLE
    print("[INFO] Successfully imported DockerManager", flush=True)
    if not DOCKER_AVAILABLE:
        print("[WARNING] Docker module is not available in the Python environment", flush=True)
except ImportError as e:
    print(f"[ERROR] Failed to import DockerManager: {e}", flush=True)
    raise

# ================== GEMINI API CONFIG ==========================================
GEMINI_API_KEY = "AIzaSyBxnzCyvlkmT8cvpHVLuW_6XSwlVT5vdzI"  # Replace with your actual API key from https://aistudio.google.com/app/apikey
GEMINI_MODEL = "gemini-2.5-flash"

# ================== PATH SETUP ==================================================
BASE_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = BASE_DIR / "backend"
FRONTEND = BASE_DIR / "frontend"
FRONTEND_DIR = os.path.join(str(BASE_DIR), "frontend")      # frontend/
DB_PATH = os.path.join(str(BACKEND_DIR), "database.db")     # backend/database.db
APP_TITLE = "Ethical Hacking Simulation Portal"

# ================== APP ===========================================================
app = FastAPI(title=APP_TITLE)

# ================== STATIC FILES ==================================================
app.mount(
    "/static",
    StaticFiles(directory=os.path.join(FRONTEND_DIR, "static")),
    name="static"
)

# ================== DEBUG ENDPOINT ================================================
@app.get("/debug")
def debug(email: str | None = None):
    return {
        "email_received": email,
        "status": "API is running"
    }

# ================== DATABASE ==================
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=45.0, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 45000")  # 45 seconds timeout for busy database
    conn.execute("PRAGMA journal_mode = WAL")    # Enable Write-Ahead Logging for better concurrency
    conn.isolation_level = "DEFERRED"  # Use deferred transactions for better concurrency
    return conn


def execute_with_retry(func, max_retries=5, retry_delay=0.1):
    """Execute a database operation with automatic retry on lock errors"""
    import time as time_module
    for attempt in range(max_retries):
        try:
            return func()
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e):
                if attempt < max_retries - 1:
                    time_module.sleep(retry_delay * (attempt + 1))  # Exponential backoff
                    continue
            raise e
    return None


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            email TEXT UNIQUE,
            password TEXT,
            level INTEGER DEFAULT 1,
            xp INTEGER DEFAULT 0,
            challenges_solved INTEGER DEFAULT 0,
            global_rank INTEGER DEFAULT 0,
            avatar TEXT DEFAULT 'avatar1',
            status TEXT DEFAULT 'active',
            is_locked INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP
        )
    """)
    
    # Add missing columns to users table if they don't exist
    try:
        cur.execute("ALTER TABLE users ADD COLUMN status TEXT DEFAULT 'active'")
    except:
        pass
    try:
        cur.execute("ALTER TABLE users ADD COLUMN is_locked INTEGER DEFAULT 0")
    except:
        pass
    try:
        cur.execute("ALTER TABLE users ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    except:
        pass
    try:
        cur.execute("ALTER TABLE users ADD COLUMN last_login TIMESTAMP")
    except:
        pass
    
    # Login history table
    cur.execute("""CREATE TABLE IF NOT EXISTS login_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        user_email TEXT NOT NULL,
        login_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        logout_time TIMESTAMP,
        ip_address TEXT,
        user_agent TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """)
    
    # Audit logs table
    cur.execute("""CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_email TEXT NOT NULL,
        action TEXT NOT NULL,
        target_user_id INTEGER,
        target_user_email TEXT,
        old_value TEXT,
        new_value TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        details TEXT
    )
    """)
    
    # Admin messages table
    cur.execute("""CREATE TABLE IF NOT EXISTS admin_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender_email TEXT NOT NULL,
        recipient_email TEXT NOT NULL,
        message_text TEXT NOT NULL,
        message_type TEXT DEFAULT 'user',
        is_read INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(sender_email) REFERENCES users(email) ON DELETE CASCADE,
        FOREIGN KEY(recipient_email) REFERENCES users(email) ON DELETE CASCADE
    )
    """)
    
    # User security settings table
    cur.execute("""CREATE TABLE IF NOT EXISTS user_security_settings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE NOT NULL,
        user_email TEXT UNIQUE NOT NULL,
        two_factor_enabled INTEGER DEFAULT 0,
        failed_login_attempts INTEGER DEFAULT 0,
        last_failed_attempt TIMESTAMP,
        password_changed_at TIMESTAMP,
        force_password_reset INTEGER DEFAULT 0,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """)
    
    cur.execute("""CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    target TEXT DEFAULT 'all',  -- all / user / admin
    created_by TEXT DEFAULT 'Admin',
    created_at TEXT
    )
    """)
    cur.execute("""CREATE TABLE IF NOT EXISTS friendships (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        requester_email TEXT NOT NULL,
        receiver_email TEXT NOT NULL,
        status TEXT DEFAULT 'pending',  -- pending / accepted / blocked
        requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        responded_at TIMESTAMP,
        UNIQUE(requester_email, receiver_email),
        FOREIGN KEY(requester_email) REFERENCES users(email) ON DELETE CASCADE,
        FOREIGN KEY(receiver_email) REFERENCES users(email) ON DELETE CASCADE
    )
    """)
    cur.execute("""CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender_email TEXT NOT NULL,
        receiver_email TEXT NOT NULL,
        message_text TEXT NOT NULL,
        is_read INTEGER DEFAULT 0,
        sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(sender_email) REFERENCES users(email) ON DELETE CASCADE,
        FOREIGN KEY(receiver_email) REFERENCES users(email) ON DELETE CASCADE
    )
    """)
    
    # Add is_read column if it doesn't exist
    try:
        cur.execute("ALTER TABLE messages ADD COLUMN is_read INTEGER DEFAULT 0")
    except:
        pass
    cur.execute("""CREATE TABLE IF NOT EXISTS chat_clear_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_email TEXT NOT NULL,
        friend_email TEXT NOT NULL,
        cleared_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_email, friend_email),
        FOREIGN KEY(user_email) REFERENCES users(email) ON DELETE CASCADE,
        FOREIGN KEY(friend_email) REFERENCES users(email) ON DELETE CASCADE
    )
    """)
    
    # ================== QUIZ TABLES ==================
    cur.execute("""CREATE TABLE IF NOT EXISTS quizzes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        description TEXT,
        category TEXT,
        difficulty TEXT,
        points INTEGER DEFAULT 10,
        passing_score INTEGER DEFAULT 70,
        time_limit INTEGER,
        is_published INTEGER DEFAULT 0,
        created_by TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    cur.execute("""CREATE TABLE IF NOT EXISTS quiz_questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        quiz_id INTEGER NOT NULL,
        question_text TEXT NOT NULL,
        question_type TEXT DEFAULT 'multiple_choice',
        question_order INTEGER,
        points_value INTEGER DEFAULT 1,
        FOREIGN KEY(quiz_id) REFERENCES quizzes(id) ON DELETE CASCADE
    )
    """)
    
    cur.execute("""CREATE TABLE IF NOT EXISTS quiz_options (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        question_id INTEGER NOT NULL,
        option_text TEXT NOT NULL,
        is_correct INTEGER DEFAULT 0,
        option_order INTEGER,
        FOREIGN KEY(question_id) REFERENCES quiz_questions(id) ON DELETE CASCADE
    )
    """)
    
    cur.execute("""CREATE TABLE IF NOT EXISTS quiz_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        quiz_id INTEGER NOT NULL,
        score INTEGER,
        total_questions INTEGER,
        percentage DECIMAL(5,2),
        passed INTEGER DEFAULT 0,
        time_spent INTEGER,
        started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        completed_at TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY(quiz_id) REFERENCES quizzes(id) ON DELETE CASCADE
    )
    """)
    
    cur.execute("""CREATE TABLE IF NOT EXISTS quiz_answers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        attempt_id INTEGER NOT NULL,
        question_id INTEGER NOT NULL,
        selected_option_id INTEGER,
        is_correct INTEGER DEFAULT 0,
        answered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(attempt_id) REFERENCES quiz_attempts(id) ON DELETE CASCADE,
        FOREIGN KEY(question_id) REFERENCES quiz_questions(id) ON DELETE CASCADE
    )
    """)
    
    conn.commit()
    conn.close()


init_db()

# notifications on the user side.
@app.get("/user/api/notifications")
def get_user_notifications(email: str):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT title, message, created_at
        FROM notifications
        WHERE target IN ('all', 'user')
        ORDER BY created_at DESC
        LIMIT 10
    """)

    rows = cur.fetchall()
    conn.close()

    return [
        {
            "title": r[0],
            "message": r[1],
            "created_at": r[2]
        }
        for r in rows
    ]

@app.get("/user/api/dashboard")
def user_dashboard_api(email: str):
    """Get comprehensive dashboard data for user"""
    user = get_user_by_email(email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    user_id, name, email, level, xp, challenges_solved, rank = user
    
    conn = get_db()
    cur = conn.cursor()
    
    # Get user's total XP (from users table)
    cur.execute("SELECT xp FROM users WHERE id = ?", (user_id,))
    xp_result = cur.fetchone()
    total_xp = xp_result[0] if xp_result else xp
    
    print(f"[DASHBOARD API] User: {name}, XP from DB: {total_xp}", flush=True)
    
    # Count total published tutorials
    cur.execute("SELECT COUNT(*) FROM tutorials WHERE is_published = 1")
    total_tutorials = cur.fetchone()[0] or 0
    print(f"[DASHBOARD] Total published tutorials: {total_tutorials}", flush=True)
    
    # Count completed tutorials
    cur.execute("SELECT COUNT(*) FROM tutorial_progress WHERE username = ? AND completed = 1", (name,))
    tutorials_completed = cur.fetchone()[0] or 0
    print(f"[DASHBOARD] Completed tutorials: {tutorials_completed}", flush=True)
    
    # Count total published challenges
    cur.execute("SELECT COUNT(*) FROM challenges WHERE is_published = 1")
    total_challenges = cur.fetchone()[0] or 0
    print(f"[DASHBOARD] Total published challenges: {total_challenges}", flush=True)
    
    # Challenges solved is already in user table
    challenges_completed = challenges_solved or 0
    print(f"[DASHBOARD] Completed challenges: {challenges_completed}", flush=True)
    
    # Count total published quizzes
    cur.execute("SELECT COUNT(*) FROM quizzes WHERE is_published = 1")
    total_quizzes = cur.fetchone()[0] or 0
    print(f"[DASHBOARD] Total published quizzes: {total_quizzes}", flush=True)
    
    # Count passed quizzes
    cur.execute("SELECT COUNT(DISTINCT quiz_id) FROM quiz_attempts WHERE user_id = ? AND passed = 1", (user_id,))
    quizzes_completed = cur.fetchone()[0] or 0
    print(f"[DASHBOARD] Completed quizzes: {quizzes_completed}", flush=True)
    
    # Calculate overall progress percentage
    total_items = total_tutorials + total_challenges + total_quizzes
    completed_items = tutorials_completed + challenges_completed + quizzes_completed
    
    print(f"[DASHBOARD] Total items: {total_items}, Completed: {completed_items}", flush=True)
    
    if total_items > 0:
        overall_progress = int((completed_items / total_items) * 100)
    else:
        overall_progress = 0
    
    print(f"[DASHBOARD] Overall progress: {overall_progress}%", flush=True)
    
    # Get daily challenge (first incomplete published challenge)
    cur.execute("""
        SELECT id, title, description, difficulty, points, estimated_time
        FROM challenges
        WHERE is_published = 1
        ORDER BY id ASC
        LIMIT 1
    """)
    daily_challenge_row = cur.fetchone()
    
    daily_challenge = None
    if daily_challenge_row:
        # Check if user completed this challenge
        cur.execute("""
            SELECT id FROM attempt_history 
            WHERE user_id = ? AND resource_id = ? AND activity_type = 'completed'
        """, (user_id, daily_challenge_row[0]))
        completed = cur.fetchone() is not None
        
        daily_challenge = {
            "id": daily_challenge_row[0],
            "title": daily_challenge_row[1],
            "description": daily_challenge_row[2],
            "difficulty": daily_challenge_row[3],
            "points": daily_challenge_row[4],
            "time": daily_challenge_row[5],
            "completed": completed
        }
    
    # Get recommendations based on difficulty level
    recommendations = []
    recommended_difficulty = 'Easy' if level <= 5 else ('Medium' if level <= 15 else 'Hard')
    
    cur.execute("""
        SELECT id, title, description, difficulty, category, points
        FROM challenges
        WHERE is_published = 1 AND difficulty = ?
        ORDER BY id ASC
        LIMIT 3
    """, (recommended_difficulty,))
    
    for rec_row in cur.fetchall():
        recommendations.append({
            "id": rec_row[0],
            "title": rec_row[1],
            "description": rec_row[2],
            "difficulty": rec_row[3],
            "category": rec_row[4],
            "points": rec_row[5]
        })
    
    conn.close()
    
    return_data = {
        "success": True,
        "username": name,
        "xp": total_xp,
        "level": level,
        "rank": rank,
        "challenges_solved": challenges_completed,
        "tutorials_completed": tutorials_completed,
        "quizzes_completed": quizzes_completed,
        "overall_progress": overall_progress,
        "daily_challenge": daily_challenge,
        "recommendations": recommendations
    }
    
    print(f"[DASHBOARD] Returning: xp={total_xp}, progress={overall_progress}%, tutorials={tutorials_completed}, challenges={challenges_completed}, quizzes={quizzes_completed}", flush=True)
    
    return return_data

def get_user_by_email(email):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, name, email, level, xp, challenges_solved, global_rank
        FROM users WHERE email=?
    """, (email,))

    user = cur.fetchone()
    conn.close()
    return user

def log_attempt(user_id, activity_type, resource_id, resource_name, resource_type):
    """Log a user's visit to a tutorial or challenge"""
    conn = get_db()
    cur = conn.cursor()
    
    cur.execute("""
        INSERT INTO attempt_history (user_id, activity_type, resource_id, resource_name, resource_type)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, activity_type, resource_id, resource_name, resource_type))
    
    conn.commit()
    conn.close()

def init_ranking_table():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS ranking (
    user_id INTEGER PRIMARY KEY,
    rank INTEGER,
    score INTEGER,
    level INTEGER,
    challenges_solved INTEGER,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

    """)

    conn.commit()
    conn.close()

#learderboard shouls be recalculated whenever the leadership.html is loaded.
def update_ranks():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        SELECT user_id
        FROM ranking
        ORDER BY score DESC, challenges_solved DESC
    """)

    users = cur.fetchall()

    rank = 1
    for (user_id,) in users:
        cur.execute(
            "UPDATE ranking SET rank=? WHERE user_id=?",
            (rank, user_id)
        )
        rank += 1

    conn.commit()
    conn.close()

#for syncing rank table to ensure that users with 0 score score exists
def sync_ranking_table():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Insert missing users into ranking
    cur.execute("""
        INSERT OR IGNORE INTO ranking (user_id, score, level, challenges_solved)
        SELECT id, xp, level, challenges_solved
        FROM users
    """)

    conn.commit()
    conn.close()

def update_global_ranks():
    conn = get_db()
    cur = conn.cursor()

    # Order users by leaderboard logic (XP first, then level)
    cur.execute("""
        SELECT id
        FROM users
        ORDER BY xp DESC, level DESC
    """)
    users = cur.fetchall()

    # Assign ranks and update both users and ranking tables
    for rank, (user_id,) in enumerate(users, start=1):
        cur.execute(
            "UPDATE users SET global_rank=? WHERE id=?",
            (rank, user_id)
        )
        
        # Also update ranking table for consistency
        cur.execute("""
            INSERT OR REPLACE INTO ranking (user_id, score, level, challenges_solved)
            SELECT id, xp, level, challenges_solved FROM users WHERE id = ?
        """, (user_id,))

    conn.commit()
    conn.close()

def init_learning_tables():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # ---------------- TUTORIALS ----------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS tutorials (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        category TEXT,
        difficulty TEXT,
        estimated_time INTEGER,
        intro TEXT,
        points INTEGER DEFAULT 0,
        is_published INTEGER DEFAULT 0,
        average_rating DECIMAL(3,2) DEFAULT 0,
        rating_count INTEGER DEFAULT 0
    )
    """)

   # ---------------- TUTORIAL CLASSES ----------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS tutorial_classes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tutorial_id INTEGER,
        title TEXT,
        class_order INTEGER,
        progress_percentage INTEGER DEFAULT 0,
        FOREIGN KEY (tutorial_id) REFERENCES tutorials(id) ON DELETE CASCADE
    )
    """)
    # ---------------- CLASS BLOCKS (ORDERED FLOW) ----------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS tutorial_blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id INTEGER,
    block_type TEXT,
    content TEXT,
    answer TEXT,
    hint TEXT,
    position TEXT,          -- ✅ REQUIRED FOR IMAGE LEFT/RIGHT
    block_order INTEGER,
    FOREIGN KEY (class_id) REFERENCES tutorial_classes(id) ON DELETE CASCADE
     )
    """)

    # ------------ TUTORIAL PROGRESS -------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS tutorial_progress (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL,
        tutorial_id INTEGER NOT NULL,
        progress INTEGER DEFAULT 0,
        completed INTEGER DEFAULT 0,
        last_accessed DATETIME,
        completed_at DATETIME,
        current_class_index INTEGER,
        UNIQUE (username, tutorial_id)
    )
    """)
    
    # ------------ TUTORIAL RATINGS --------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS tutorial_ratings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        tutorial_id INTEGER NOT NULL,
        rating INTEGER NOT NULL,
        created_at DATETIME,
        UNIQUE (user_id, tutorial_id)
    )
    """)

    # ✅ ADD MIGRATION: Add position column if it doesn't exist
    try:
        cur.execute("ALTER TABLE tutorial_blocks ADD COLUMN position TEXT DEFAULT 'left'")
    except sqlite3.OperationalError as e:
        if "duplicate column name" not in str(e):
            raise
        # Column already exists, continue

    # ✅ ADD MIGRATION: Add is_published column if it doesn't exist
    try:
        cur.execute("ALTER TABLE tutorials ADD COLUMN is_published INTEGER DEFAULT 0")
    except sqlite3.OperationalError as e:
        if "duplicate column name" not in str(e):
            raise
        # Column already exists, continue

    # Add progress_percentage column to tutorial_classes if it doesn't exist
    try:
        cur.execute("ALTER TABLE tutorial_classes ADD COLUMN progress_percentage INTEGER DEFAULT 0")
    except sqlite3.OperationalError as e:
        if "duplicate column name" not in str(e):
            raise
        # Column already exists, continue

    # Add current_class_index column to tutorial_progress if it doesn't exist
    try:
        cur.execute("ALTER TABLE tutorial_progress ADD COLUMN current_class_index INTEGER DEFAULT 0")
    except sqlite3.OperationalError as e:
        if "duplicate column name" not in str(e):
            raise
        # Column already exists, continue

    # ------------ SAVED TUTORIALS -----------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS saved_tutorials (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        tutorial_id INTEGER NOT NULL,
        saved_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (user_id, tutorial_id),
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (tutorial_id) REFERENCES tutorials(id) ON DELETE CASCADE
    )
    """)

    # ------------ ATTEMPT HISTORY -----------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS attempt_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        activity_type TEXT NOT NULL,
        resource_id INTEGER NOT NULL,
        resource_name TEXT NOT NULL,
        resource_type TEXT NOT NULL,
        visited_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """)

    conn.commit()
    conn.close()

#calling
init_db()
init_learning_tables()
init_ranking_table()

def update_challenge_solves(challenge_id):
    """Update the challenge_solves table when a challenge is solved"""
    try:
        conn = get_db()
        cur = conn.cursor()
        
        # Check if challenge exists in challenge_solves
        cur.execute("SELECT id FROM challenge_solves WHERE challenge_id = ?", (challenge_id,))
        exists = cur.fetchone()
        
        if not exists:
            # Insert new entry
            cur.execute("""
                SELECT id, title, category FROM challenges WHERE id = ?
            """, (challenge_id,))
            challenge = cur.fetchone()
            
            if challenge:
                cur.execute("""
                    INSERT INTO challenge_solves (challenge_id, title, category, count)
                    VALUES (?, ?, ?, 1)
                """, (challenge_id, challenge[1], challenge[2]))
        else:
            # Update count - count users who have completed this challenge
            cur.execute("""
                UPDATE challenge_solves
                SET count = (
                    SELECT COUNT(DISTINCT user_id)
                    FROM user_challenges
                    WHERE challenge_id = ? AND progress >= 100
                )
                WHERE challenge_id = ?
            """, (challenge_id, challenge_id))
        
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[ERROR] Failed to update challenge_solves: {e}", flush=True)

def init_challenges_table():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Create challenges table - NO auto-population
    cur.execute("""
        CREATE TABLE IF NOT EXISTS challenges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            category TEXT,
            difficulty TEXT,
            points INTEGER,
            description TEXT,
            flag TEXT,
            docker_image TEXT NOT NULL,
            is_published INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_challenges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            challenge_id INTEGER,
            solved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, challenge_id),
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(challenge_id) REFERENCES challenges(id)
        )
    """)

    # Track running Docker containers
    cur.execute("""
        CREATE TABLE IF NOT EXISTS running_containers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            challenge_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            container_id TEXT NOT NULL,
            container_name TEXT,
            port_mapping TEXT,
            status TEXT DEFAULT 'running',
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            stopped_at TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(challenge_id) REFERENCES challenges(id)
        )
    """)

    # Add columns if they don't exist
    try:
        cur.execute("ALTER TABLE running_containers ADD COLUMN duration_minutes INTEGER DEFAULT 30")
    except:
        pass
    
    # Add progress column to user_challenges if it doesn't exist
    try:
        cur.execute("ALTER TABLE user_challenges ADD COLUMN progress INTEGER DEFAULT 0")
    except:
        pass

    # Create challenge_solves table - tracks completions per challenge
    cur.execute("""
        CREATE TABLE IF NOT EXISTS challenge_solves (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            challenge_id INTEGER UNIQUE NOT NULL,
            title TEXT NOT NULL,
            category TEXT,
            count INTEGER DEFAULT 0,
            FOREIGN KEY(challenge_id) REFERENCES challenges(id) ON DELETE CASCADE
        )
    """)

    # Add missing columns if they don't exist
    try:
        cur.execute("ALTER TABLE challenge_solves ADD COLUMN title TEXT")
    except:
        pass
    try:
        cur.execute("ALTER TABLE challenge_solves ADD COLUMN category TEXT")
    except:
        pass
    try:
        cur.execute("ALTER TABLE challenge_solves ADD COLUMN challenge_id INTEGER")
    except:
        pass

    conn.commit()
    conn.close()





init_challenges_table()

@app.post("/admin/tutorials/{tutorial_id}/classes")
def save_tutorial_classes(tutorial_id: int, payload: dict = Body(...)):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()

    try:
        classes = payload.get("classes", [])
        if not classes:
            raise Exception("No classes provided")

        # Ensure tutorial exists
        cur.execute("SELECT id FROM tutorials WHERE id=?", (tutorial_id,))
        if not cur.fetchone():
            raise Exception("Tutorial does not exist")

        # BEGIN TRANSACTION
        conn.execute("BEGIN")

        # Clear old data
        cur.execute("""
            DELETE FROM tutorial_blocks
            WHERE class_id IN (
                SELECT id FROM tutorial_classes WHERE tutorial_id=?
            )
        """, (tutorial_id,))
        cur.execute("DELETE FROM tutorial_classes WHERE tutorial_id=?", (tutorial_id,))

        # Insert new data
        for class_order, cls in enumerate(classes):
            progress = cls.get("progress_percentage", 0)
            cur.execute("""
                INSERT INTO tutorial_classes (tutorial_id, title, class_order, progress_percentage)
                VALUES (?, ?, ?, ?)
            """, (tutorial_id, cls["title"], class_order, progress))

            class_id = cur.lastrowid

            for block_order, block in enumerate(cls["blocks"]):
                content = None
                answer = None
                hint = None
                position = "left"
                
                if block["type"] == "content":
                    content = block.get("text")
                elif block["type"] == "knowledge":
                    content = block.get("text")
                    answer = block.get("answer")
                    hint = block.get("hint")
                elif block["type"] == "image":
                    content = block.get("image_name")
                    hint = block.get("description")
                    position = block.get("position", "left")
                
                cur.execute("""
                    INSERT INTO tutorial_blocks
                    (class_id, block_type, content, answer, hint, position, block_order)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    class_id,
                    block["type"],
                    content,
                    answer,
                    hint,
                    position,
                    block_order
                ))

        conn.commit()
        return {"status": "saved", "message": "Tutorial classes saved successfully"}

    except Exception as e:
        conn.rollback()
        print("SAVE ERROR:", str(e))
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        conn.close()


 # ================== ADMIN LOGIN ==================

@app.get("/admin/login", response_class=HTMLResponse)
def admin_login():
    return open(
        os.path.join(FRONTEND_DIR, "admin_login.html"),
        encoding="utf-8"
    ).read()


# ================== ADMIN FORGOT PASSWORD ==================

@app.get("/admin/forgot_password", response_class=HTMLResponse)
def admin_forgot_password():
    return open(
        os.path.join(FRONTEND_DIR, "admin_forgot_password.html"),
        encoding="utf-8"
    ).read()


@app.post("/admin/forgot_password")
def admin_forgot_password_post(email: str = Form(...)):
    # For project demo: assume email is valid
    return RedirectResponse(
        f"/admin/reset_password?email={email}",
        status_code=302
    )


@app.post("/admin/login")
def admin_login_post(
    email: str = Form(...),
    password: str = Form(...)
):
    conn = get_db()
    cur = conn.cursor()
    update_global_ranks()

    # First check if email exists
    cur.execute("SELECT email, password FROM admin WHERE email=?", (email,))
    admin = cur.fetchone()
    conn.close()

    if not admin:
        # Email doesn't exist
        return RedirectResponse("/admin/login?error=invalid_email", status_code=302)
    
    # Email exists, check password
    if admin[1] != password:
        # Password is incorrect
        return RedirectResponse("/admin/login?error=invalid_password", status_code=302)

    # Both email and password are correct
    return RedirectResponse("/admin/dashboard", status_code=302)

@app.get("/admin/tutorials/{tutorial_id}/classes")
def get_tutorial_classes(tutorial_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT id, title, progress_percentage
            FROM tutorial_classes
            WHERE tutorial_id=?
            ORDER BY class_order
        """, (tutorial_id,))
    except sqlite3.OperationalError:
        # Column doesn't exist yet, query without it
        cur.execute("""
            SELECT id, title
            FROM tutorial_classes
            WHERE tutorial_id=?
            ORDER BY class_order
        """, (tutorial_id,))
        # Fetch results and add default progress
        classes = [(row[0], row[1], 0) for row in cur.fetchall()]
    else:
        classes = cur.fetchall()

    result = []

    for class_id, title, progress_percentage in classes:
        cur.execute("""
            SELECT block_type, content, answer, hint, position
            FROM tutorial_blocks
            WHERE class_id=?
            ORDER BY block_order
        """, (class_id,))
        blocks = cur.fetchall()

        formatted_blocks = []

        for block_type, content, answer, hint, position in blocks:
            if block_type == "content":
                formatted_blocks.append({
                    "type": "content",
                    "text": content
                })

            elif block_type == "knowledge":
                formatted_blocks.append({
                    "type": "knowledge",
                    "text": content,
                    "answer": answer,
                    "hint": hint
                })

            elif block_type == "image":
                formatted_blocks.append({
                    "type": "image",
                    "image_name": content,
                    "image_url": f"/static/uploads/{content}" if content else "",
                    "description": hint,
                    "position": position or "left"
                })

        result.append({
            "title": title,
            "blocks": formatted_blocks,
            "progress_percentage": progress_percentage
        })

    conn.close()
    return {"classes": result}


# ================== ADMIN DASHBOARD ==================

@app.get("/admin/dashboard", response_class=HTMLResponse)
def admin_dashboard():
    return open(
        os.path.join(FRONTEND_DIR, "admin_dashboard.html"),
        encoding="utf-8"
    ).read()

# ================== ADMIN RESET PASSWORD ==================

@app.get("/admin/reset_password", response_class=HTMLResponse)
def admin_reset_password(email: str):
    html = open(
        os.path.join(FRONTEND_DIR, "admin_reset_password.html"),
        encoding="utf-8"
    ).read()

    return HTMLResponse(html.replace("{{EMAIL}}", email))


@app.post("/admin/reset_password")
def admin_reset_password_post(
    email: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...)
):
    if new_password != confirm_password:
        return HTMLResponse("Passwords do not match", status_code=400)

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE admin SET password=? WHERE email=?",
        (new_password, email)
    )
    conn.commit()
    conn.close()

    return RedirectResponse("/admin/login", status_code=302)

# ================== ADMIN: LIST TUTORIALS ==================
@app.get("/admin/tutorials/list")
def list_tutorials():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        SELECT id, title, category, difficulty, estimated_time, intro, points
        FROM tutorials
        ORDER BY id ASC
    """)

    tutorials = []

    for row in cur.fetchall():
        tutorials.append({
            "id": row[0],
            "title": row[1],
            "category": row[2],
            "difficulty": row[3],
            "estimated_time": row[4],
            "intro": row[5],
            "points": row[6]
        })

    conn.close()
    return tutorials


# ================== ADMIN: PUBLISH TUTORIAL ==================
@app.post("/admin/tutorials/{tutorial_id}/publish")
def publish_tutorial(tutorial_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    try:
        # Update the tutorial to published status
        cur.execute(
            "UPDATE tutorials SET is_published=1 WHERE id=?",
            (tutorial_id,)
        )
        
        if cur.rowcount == 0:
            conn.close()
            return JSONResponse({"error": "Tutorial not found"}, status_code=404)
        
        conn.commit()
        conn.close()
        
        return JSONResponse({"success": True, "message": "Tutorial published successfully"})
    except Exception as e:
        conn.close()
        return JSONResponse({"error": str(e)}, status_code=500)


# ================== ADMIN: UNPUBLISH TUTORIAL ==================
@app.post("/admin/tutorials/{tutorial_id}/unpublish")
def unpublish_tutorial(tutorial_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    try:
        # Update the tutorial to unpublished status
        cur.execute(
            "UPDATE tutorials SET is_published=0 WHERE id=?",
            (tutorial_id,)
        )
        
        if cur.rowcount == 0:
            conn.close()
            return JSONResponse({"error": "Tutorial not found"}, status_code=404)
        
        conn.commit()
        conn.close()
        
        return JSONResponse({"success": True, "message": "Tutorial unpublished successfully"})
    except Exception as e:
        conn.close()
        return JSONResponse({"error": str(e)}, status_code=500)


# ================== ADMIN: UPGRADE & PUBLISH TUTORIAL ==================
@app.post("/admin/tutorials/{tutorial_id}/upgrade-publish")
def upgrade_publish_tutorial(tutorial_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    try:
        # Get tutorial info first
        cur.execute(
            "SELECT id, title, points FROM tutorials WHERE id=?",
            (tutorial_id,)
        )
        tutorial = cur.fetchone()
        
        if not tutorial:
            conn.close()
            return JSONResponse({"error": "Tutorial not found"}, status_code=404)
        
        # Update the tutorial to published status and increase points by 50%
        current_points = tutorial[2] or 0
        upgraded_points = int(current_points * 1.5)
        
        cur.execute(
            "UPDATE tutorials SET is_published=1, points=? WHERE id=?",
            (upgraded_points, tutorial_id)
        )
        
        conn.commit()
        conn.close()
        
        return JSONResponse({
            "success": True, 
            "message": f"Tutorial upgraded & published successfully! Points increased from {current_points} to {upgraded_points}"
        })
    except Exception as e:
        conn.close()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/admin/tutorials/content", response_class=HTMLResponse)
def admin_tutorial_content():
    return (FRONTEND / "admin_tutorial_content.html").read_text(encoding="utf-8")

@app.get("/admin/tutorials/{tutorial_id}/preview", response_class=HTMLResponse)
def admin_tutorial_preview(tutorial_id: int):
    return (FRONTEND / "admin_tutorial_preview.html").read_text(encoding="utf-8")

# ================== LANDING ==================
@app.get("/", response_class=HTMLResponse)
def home():
    return open(os.path.join(FRONTEND_DIR, "index.html"), encoding="utf-8").read()


# ================== LOGIN ==================
@app.get("/user/login", response_class=HTMLResponse)
def user_login():
    return open(os.path.join(FRONTEND_DIR, "user_login.html"), encoding="utf-8").read()


@app.post("/user/login")
async def user_login_post(request: Request, email: str = Form(...), password: str = Form(...)):
    conn = get_db()
    cur = conn.cursor()

    # First check if email exists and get user details including locked status
    cur.execute("SELECT id, email, password, is_locked, status FROM users WHERE email=?", (email,))
    user = cur.fetchone()
    
    if not user:
        # Email doesn't exist
        conn.close()
        return RedirectResponse("/user/login?error=invalid_email", status_code=302)
    
    user_id = user[0]
    user_email = user[1]
    user_password = user[2]
    is_locked = user[3]
    user_status = user[4]
    
    # Email exists, check password
    if user_password != password:
        # Password is incorrect
        conn.close()
        return RedirectResponse("/user/login?error=invalid_password", status_code=302)

    # Check if user account is locked
    if is_locked:
        conn.close()
        return RedirectResponse("/user/login?error=account_locked", status_code=302)
    
    # Check if user is banned
    if user_status == 'banned':
        conn.close()
        return RedirectResponse("/user/login?error=account_banned", status_code=302)
    
    # Both email and password are correct and account is not locked - record login
    
    try:
        ip_address = request.client.host if request.client else "Unknown"
        
        # Update last_login timestamp on users table
        cur.execute("""
            UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?
        """, (user_id,))
        
        # Insert login history
        cur.execute("""
            INSERT INTO login_history (user_id, user_email, login_time, ip_address)
            VALUES (?, ?, datetime('now'), ?)
        """, (user_id, user_email, ip_address))
        
        conn.commit()
    except Exception as e:
        print(f"[LOGIN ERROR] Failed to record login history: {str(e)}")
    finally:
        conn.close()
    
    return RedirectResponse(f"/user/dashboard?email={email}", status_code=302)

#==========================admin tutorial preview page==========================#
@app.get("/tutorials/preview/{tutorial_id}")
def tutorial_preview_page(tutorial_id: int):
    file_path = os.path.join(FRONTEND_DIR, "admin_tutorial_preview.html")
    return FileResponse(file_path)

# ================== REGISTER ==================
@app.get("/user/register", response_class=HTMLResponse)
def user_register():
    return open(os.path.join(FRONTEND_DIR, "user_register.html"), encoding="utf-8").read()


@app.post("/user/register")
def user_register_post(
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...)
):
    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute(
            "INSERT INTO users (name, email, password) VALUES (?, ?, ?)",
            (name, email, password)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return HTMLResponse("User already exists", status_code=400)

    conn.close()
    return RedirectResponse("/user/login", status_code=302)


# ================== DASHBOARD ==================
@app.get("/user/dashboard", response_class=HTMLResponse)
def user_dashboard(email: str):
    user = get_user_by_email(email)
    if not user:
        return RedirectResponse("/user/login")

    user_id, name, email, level, xp, challenges, rank = user
    
    # Calculate total score from completed tutorials only
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT COALESCE(SUM(t.points), 0) as total_score
        FROM tutorial_progress tp
        LEFT JOIN tutorials t ON tp.tutorial_id = t.id
        WHERE tp.username = ? AND tp.completed = 1
    """, (name,))
    score_result = cur.fetchone()
    total_score = score_result[0] if score_result else 0
    conn.close()

    html = open(os.path.join(FRONTEND_DIR, "user_dashboard.html"), encoding="utf-8").read()

    return HTMLResponse(
        html.replace("{{USERNAME}}", name)
            .replace("{{EMAIL}}", email)
            .replace("{{LEVEL}}", str(level))
            .replace("{{XP}}", str(total_score))
            .replace("{{CHALLENGES}}", str(challenges))
            .replace("{{RANK}}", str(rank))
    )


# ================== PROFILE ==================
AVATAR_MAP = {
    "avatar1": "🦸",
    "avatar2": "🧙",
    "avatar3": "🕵️",
    "avatar4": "👨‍💻",
    "avatar5": "🔮"
}

@app.get("/user/profile", response_class=HTMLResponse)
def user_profile(email: str):
    user = get_user_by_email(email)
    if not user:
        return RedirectResponse("/user/login")

    user_id, name, email, level, xp, challenges, rank = user
    
    # Get avatar from database
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT avatar FROM users WHERE email=?", (email,))
        avatar_result = cur.fetchone()
        avatar = avatar_result[0] if avatar_result and avatar_result[0] else "avatar1"
    except:
        avatar = "avatar1"
    
    # Total score is already in xp (tutorial points are added when tutorial is completed)
    total_score = xp
    conn.close()
    
    # Map avatar to seed
    avatar_seeds = {
        'avatar1': 'character1',
        'avatar2': 'character2',
        'avatar3': 'character3',
        'avatar4': 'character4',
        'avatar5': 'character5',
        'avatar6': 'character6',
        'avatar7': 'character7',
        'avatar8': 'character8',
        'avatar9': 'character9',
        'avatar10': 'character10'
    }
    seed = avatar_seeds.get(avatar, 'character1')
    
    # Create image HTML for avatar with professional business style
    avatar_html = f'<img src="https://api.dicebear.com/7.x/avataaars/svg?seed={seed}&scale=85" alt="Avatar" onerror="this.textContent=\'👤\'">'

    html = open(os.path.join(FRONTEND_DIR, "user_profile.html"), encoding="utf-8").read()

    response = HTMLResponse(
        html.replace("{{USERNAME}}", name)
            .replace("{{EMAIL}}", email)
            .replace("{{LEVEL}}", str(level))
            .replace("{{XP}}", str(total_score))
            .replace("{{CHALLENGES}}", str(challenges))
            .replace("{{RANK}}", str(rank))
            .replace("{{AVATAR_HTML}}", avatar_html),
    )
    
    # Add no-cache headers to prevent browser caching
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, public, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    
    return response


# ================== PROFILE API (JSON) ==================
@app.get("/api/user/profile")
def get_user_profile_json(email: str):
    """Get user profile data as JSON - includes updated score after challenge completion"""
    user = get_user_by_email(email)
    if not user:
        return JSONResponse({"success": False, "error": "User not found"}, status_code=404)

    user_id, name, email, level, xp, challenges, rank = user
    
    # Get avatar from database
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT avatar FROM users WHERE email=?", (email,))
        avatar_result = cur.fetchone()
        avatar = avatar_result[0] if avatar_result and avatar_result[0] else "avatar1"
    except:
        avatar = "avatar1"
    
    # Count completed tutorials
    cur.execute("""
        SELECT COUNT(*) as tutorials_count
        FROM tutorial_progress
        WHERE username = ? AND completed = 1
    """, (name,))
    tutorials_result = cur.fetchone()
    tutorials_completed = tutorials_result[0] if tutorials_result else 0
    print(f"DEBUG [API]: User {name} - tutorials_completed = {tutorials_completed}")
    
    # Count completed quizzes (passed attempts) - use user_id not username
    cur.execute("""
        SELECT COUNT(DISTINCT quiz_id) as quizzes_count
        FROM quiz_attempts
        WHERE user_id = ? AND passed = 1
    """, (user_id,))
    quizzes_result = cur.fetchone()
    quizzes_completed = quizzes_result[0] if quizzes_result else 0
    print(f"DEBUG [API]: User {name} (ID: {user_id}) - quizzes_completed = {quizzes_completed}")
    
    # Total score is already in xp (tutorial points are added when tutorial is completed)
    total_score = xp
    conn.close()
    
    return JSONResponse({
        "success": True,
        "user": {
            "name": name,
            "email": email,
            "level": level,
            "xp": xp,
            "total_score": total_score,
            "challenges_solved": challenges,
            "tutorials_completed": tutorials_completed,
            "quizzes_completed": quizzes_completed,
            "rank": rank,
            "avatar": avatar
        }
    })


# ================== FRIENDS PAGE ==================
@app.get("/user/friends", response_class=HTMLResponse)
def user_friends(email: str):
    """Friends page with search, pending requests, and friends list"""
    user = get_user_by_email(email)
    if not user:
        return RedirectResponse("/user/login")

    user_id, name, email, level, xp, challenges, rank = user

    html = open(os.path.join(FRONTEND_DIR, "user_friends.html"), encoding="utf-8").read()

    return HTMLResponse(
        html.replace("{{USERNAME}}", name)
            .replace("{{EMAIL}}", email)
            .replace("{{LEVEL}}", str(level))
    )


# ================== EDIT PROFILE ==================
@app.get("/user/edit-profile", response_class=HTMLResponse)
def edit_profile(email: str):
    user = get_user_by_email(email)
    if not user:
        return RedirectResponse("/user/login")

    user_id, name, email, *rest = user
    
    # Get avatar from database (with default fallback)
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT avatar FROM users WHERE email=?", (email,))
        avatar_result = cur.fetchone()
        current_avatar = avatar_result[0] if avatar_result and avatar_result[0] else "avatar1"
    except:
        current_avatar = "avatar1"
    conn.close()

    html = open(os.path.join(FRONTEND_DIR, "edit_profile.html"), encoding="utf-8").read()

    # Replace placeholders
    html = html.replace("{{USERNAME}}", name)
    html = html.replace("{{EMAIL}}", email)
    html = html.replace("{{AVATAR_VALUE}}", current_avatar)

    return HTMLResponse(html)


@app.post("/user/edit-profile")
def edit_profile_post(
    old_email: str = Form(...),
    name: str = Form(...),
    email: str = Form(...),
    avatar: str = Form(default="avatar1")
):
    print(f"\n=== EDIT PROFILE REQUEST ===")
    print(f"Old Email: {old_email}")
    print(f"New Name: {name}")
    print(f"New Email: {email}")
    print(f"Avatar: {avatar}")
    print("==========================\n")
    
    conn = get_db()
    cur = conn.cursor()

    try:
        # Validate avatar value - accept all 10 avatars
        if avatar not in ["avatar1", "avatar2", "avatar3", "avatar4", "avatar5", "avatar6", "avatar7", "avatar8", "avatar9", "avatar10"]:
            print(f"Avatar '{avatar}' is invalid, using default avatar1")
            avatar = "avatar1"

        # Check if new email already exists (and is different from old email)
        if email != old_email:
            cur.execute("SELECT id FROM users WHERE email=?", (email,))
            if cur.fetchone():
                # Email already taken, redirect back with error
                print(f"Email {email} already exists")
                conn.close()
                return RedirectResponse(f"/user/edit-profile?email={old_email}&error=email_exists", status_code=302)

        # Perform the update
        print(f"Updating user {old_email} with new data...")
        cur.execute(
            "UPDATE users SET name=?, email=?, avatar=? WHERE email=?",
            (name, email, avatar, old_email)
        )
        
        rows_affected = cur.rowcount
        print(f"Rows affected: {rows_affected}")

        conn.commit()
        print(f"Database committed successfully")
        conn.close()

        print(f"Redirecting to profile page for {email}")
        return RedirectResponse(f"/user/profile?email={email}", status_code=302)
    
    except Exception as e:
        print(f"Edit profile error: {str(e)}")
        import traceback
        traceback.print_exc()
        if conn:
            conn.close()
        # Return to edit profile page with error message
        return RedirectResponse(f"/user/edit-profile?email={old_email}&error=update_failed", status_code=302)


# ================== CHANGE PASSWORD ==================
@app.get("/user/change-password", response_class=HTMLResponse)
def change_password(email: str):
    html = open(os.path.join(FRONTEND_DIR, "change_password.html"), encoding="utf-8").read()
    return HTMLResponse(html.replace("{{EMAIL}}", email))


@app.post("/user/change-password")
def change_password_post(
    email: str = Form(...),
    new_password: str = Form(...)
):
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "UPDATE users SET password=? WHERE email=?",
        (new_password, email)
    )

    conn.commit()
    conn.close()

    return RedirectResponse("/user/login", status_code=302)


@app.get("/user/delete-account")
def delete_account(email: str):
    """Delete user account from database"""
    try:
        conn = get_db()
        cur = conn.cursor()

        # Delete user from database
        cur.execute("DELETE FROM users WHERE email=?", (email,))

        conn.commit()
        conn.close()

        # Return success response
        return JSONResponse({"status": "success"})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)


# ================= FRIEND SYSTEM ==================

@app.post("/user/api/friend-request")
def send_friend_request(sender_email: str = Form(...), receiver_email: str = Form(...)):
    """Send a friend request"""
    try:
        if sender_email == receiver_email:
            return JSONResponse({"status": "error", "message": "Cannot add yourself as friend"}, status_code=400)
        
        conn = get_db()
        cur = conn.cursor()

        # Check if receiver exists
        cur.execute("SELECT email FROM users WHERE email=?", (receiver_email,))
        if not cur.fetchone():
            return JSONResponse({"status": "error", "message": "User not found"}, status_code=404)

        # Check if friendship already exists
        cur.execute("""
            SELECT status FROM friendships 
            WHERE (requester_email=? AND receiver_email=?) 
            OR (requester_email=? AND receiver_email=?)
        """, (sender_email, receiver_email, receiver_email, sender_email))
        
        existing = cur.fetchone()
        if existing:
            return JSONResponse({"status": "error", "message": "Friendship already exists"}, status_code=400)

        # Create friend request
        cur.execute("""
            INSERT INTO friendships (requester_email, receiver_email, status)
            VALUES (?, ?, 'pending')
        """, (sender_email, receiver_email))

        conn.commit()
        conn.close()

        return JSONResponse({"status": "success", "message": "Friend request sent"})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)


@app.post("/user/api/accept-friend-request")
def accept_friend_request(user_email: str = Form(...), requester_email: str = Form(...)):
    """Accept a friend request"""
    try:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            UPDATE friendships 
            SET status='accepted', responded_at=CURRENT_TIMESTAMP
            WHERE requester_email=? AND receiver_email=? AND status='pending'
        """, (requester_email, user_email))

        conn.commit()
        conn.close()

        return JSONResponse({"status": "success", "message": "Friend request accepted"})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)


@app.post("/user/api/reject-friend-request")
def reject_friend_request(user_email: str = Form(...), requester_email: str = Form(...)):
    """Reject a friend request"""
    try:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            DELETE FROM friendships 
            WHERE requester_email=? AND receiver_email=? AND status='pending'
        """, (requester_email, user_email))

        conn.commit()
        conn.close()

        return JSONResponse({"status": "success", "message": "Friend request rejected"})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)


@app.post("/user/api/cancel-friend-request")
def cancel_friend_request(sender_email: str = Form(...), receiver_email: str = Form(...)):
    """Cancel a sent friend request"""
    try:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            DELETE FROM friendships 
            WHERE requester_email=? AND receiver_email=? AND status='pending'
        """, (sender_email, receiver_email))

        conn.commit()
        conn.close()

        return JSONResponse({"status": "success", "message": "Friend request cancelled"})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)


@app.post("/user/api/remove-friend")
def remove_friend(user_email: str = Form(...), friend_email: str = Form(...)):
    """Remove a friend"""
    try:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            DELETE FROM friendships 
            WHERE (requester_email=? AND receiver_email=?) 
            OR (requester_email=? AND receiver_email=?)
            AND status='accepted'
        """, (user_email, friend_email, friend_email, user_email))

        conn.commit()
        conn.close()

        return JSONResponse({"status": "success", "message": "Friend removed"})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)


@app.get("/user/api/friends")
def get_friends(email: str):
    """Get list of accepted friends"""
    try:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT CASE 
                WHEN requester_email=? THEN receiver_email
                ELSE requester_email
            END as friend_email
            FROM friendships
            WHERE (requester_email=? OR receiver_email=?) AND status='accepted'
        """, (email, email, email))

        friends = cur.fetchall()
        conn.close()

        return JSONResponse({"friends": [f[0] for f in friends]})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)


@app.get("/user/api/pending-requests")
def get_pending_requests(email: str):
    """Get pending friend requests"""
    try:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT requester_email FROM friendships
            WHERE receiver_email=? AND status='pending'
            ORDER BY requested_at DESC
        """, (email,))

        requests = cur.fetchall()
        conn.close()

        return JSONResponse({"requests": [r[0] for r in requests]})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)


@app.get("/user/api/friend-status")
def get_friend_status(email: str, friend_email: str):
    """Check friendship status with another user"""
    try:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT status FROM friendships
            WHERE (requester_email=? AND receiver_email=?)
            OR (requester_email=? AND receiver_email=?)
        """, (email, friend_email, friend_email, email))

        result = cur.fetchone()
        conn.close()

        if result:
            return JSONResponse({"status": result[0]})
        return JSONResponse({"status": "none"})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)


@app.get("/user/api/search-users")
def search_users(query: str, email: str):
    """Search for users by email or name"""
    try:
        if len(query) < 2:
            return JSONResponse({"users": []})

        conn = get_db()
        cur = conn.cursor()

        # Search by email or name, exclude self
        cur.execute("""
            SELECT email, name, level FROM users
            WHERE (email LIKE ? OR name LIKE ?)
            AND email != ?
            LIMIT 10
        """, (f"%{query}%", f"%{query}%", email))

        users = cur.fetchall()
        
        # For each user, get friendship status
        result_users = []
        for user_email, user_name, user_level in users:
            cur.execute("""
                SELECT status FROM friendships
                WHERE (requester_email=? AND receiver_email=?)
                OR (requester_email=? AND receiver_email=?)
            """, (email, user_email, user_email, email))
            
            status = cur.fetchone()
            friend_status = status[0] if status else "none"

            result_users.append({
                "email": user_email,
                "name": user_name,
                "level": user_level,
                "status": friend_status
            })

        conn.close()
        return JSONResponse({"users": result_users})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)


@app.get("/user/api/all-users")
def get_all_users(email: str):
    """Get all users from the database with their friendship status"""
    try:
        conn = get_db()
        cur = conn.cursor()

        # Get all users except self
        cur.execute("""
            SELECT email, name, level FROM users
            WHERE email != ?
            ORDER BY name ASC
        """, (email,))

        users = cur.fetchall()
        
        # For each user, get friendship status
        result_users = []
        for user_email, user_name, user_level in users:
            cur.execute("""
                SELECT status FROM friendships
                WHERE (requester_email=? AND receiver_email=?)
                OR (requester_email=? AND receiver_email=?)
            """, (email, user_email, user_email, email))
            
            status = cur.fetchone()
            friend_status = status[0] if status else "none"

            result_users.append({
                "email": user_email,
                "name": user_name,
                "level": user_level,
                "status": friend_status
            })

        conn.close()
        return JSONResponse({"users": result_users})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)


# ================= MESSAGING SYSTEM ==================

@app.post("/user/api/send-message")
def send_message(sender_email: str = Form(...), receiver_email: str = Form(...), message_text: str = Form(...)):
    """Send a message to a friend"""
    try:
        if not message_text.strip():
            return JSONResponse({"status": "error", "message": "Message cannot be empty"}, status_code=400)

        conn = get_db()
        cur = conn.cursor()

        # Check if users are friends
        cur.execute("""
            SELECT status FROM friendships
            WHERE (requester_email=? AND receiver_email=? AND status='accepted')
            OR (requester_email=? AND receiver_email=? AND status='accepted')
        """, (sender_email, receiver_email, receiver_email, sender_email))

        friendship = cur.fetchone()
        if not friendship:
            conn.close()
            return JSONResponse({"status": "error", "message": "You must be friends to send messages"}, status_code=403)

        # Insert message
        cur.execute("""
            INSERT INTO messages (sender_email, receiver_email, message_text)
            VALUES (?, ?, ?)
        """, (sender_email, receiver_email, message_text))

        conn.commit()
        conn.close()

        return JSONResponse({"status": "success", "message": "Message sent"})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)


@app.get("/user/api/messages")
def get_messages(email: str, friend_email: str):
    """Get all messages between the user and a friend"""
    try:
        conn = get_db()
        cur = conn.cursor()

        # Check if user has cleared chat history with this friend
        cur.execute("""
            SELECT cleared_at FROM chat_clear_history
            WHERE user_email=? AND friend_email=?
        """, (email, friend_email))
        
        clear_record = cur.fetchone()
        cleared_at = clear_record[0] if clear_record else None

        # Get all messages between the two users, ordered by timestamp
        if cleared_at:
            # Only get messages sent after the clear timestamp
            cur.execute("""
                SELECT id, sender_email, message_text, sent_at, is_read
                FROM messages
                WHERE ((sender_email=? AND receiver_email=?) OR (sender_email=? AND receiver_email=?))
                AND sent_at > ?
                ORDER BY sent_at ASC
            """, (email, friend_email, friend_email, email, cleared_at))
        else:
            # Get all messages
            cur.execute("""
                SELECT id, sender_email, message_text, sent_at, is_read
                FROM messages
                WHERE (sender_email=? AND receiver_email=?) 
                OR (sender_email=? AND receiver_email=?)
                ORDER BY sent_at ASC
            """, (email, friend_email, friend_email, email))

        messages = cur.fetchall()
        
        # Mark messages from friend as read
        for msg_id, sender_email, message_text, sent_at, is_read in messages:
            if sender_email == friend_email and is_read == 0:
                cur.execute("""
                    UPDATE messages SET is_read = 1 WHERE id = ?
                """, (msg_id,))
        
        conn.commit()

        # Format messages - update is_read status for marked messages
        formatted_messages = []
        for msg_id, sender_email, message_text, sent_at, is_read in messages:
            # Parse timestamp and format it
            try:
                # Convert timestamp to readable format
                from datetime import datetime
                msg_time = datetime.fromisoformat(sent_at).strftime("%I:%M %p")
            except:
                msg_time = sent_at

            # Update is_read if this is a message from friend that we just marked as read
            if sender_email == friend_email and is_read == 0:
                is_read = 1

            formatted_messages.append({
                "id": msg_id,
                "sender_email": sender_email,
                "text": message_text,
                "time": msg_time,
                "sent_at": sent_at,
                "type": "sent" if sender_email == email else "received",
                "is_read": is_read
            })

        conn.close()
        return JSONResponse({"messages": formatted_messages})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)


@app.get("/user/api/new-messages")
def get_new_messages(email: str, friend_email: str, last_timestamp: str = None):
    """Get only new messages since last_timestamp"""
    try:
        conn = get_db()
        cur = conn.cursor()

        # Check if user has cleared chat history with this friend
        cur.execute("""
            SELECT cleared_at FROM chat_clear_history
            WHERE user_email=? AND friend_email=?
        """, (email, friend_email))
        
        clear_record = cur.fetchone()
        cleared_at = clear_record[0] if clear_record else None

        if last_timestamp:
            # Get messages after the last timestamp and after clear timestamp
            if cleared_at:
                # Only get messages sent after both the clear and last timestamp
                cur.execute("""
                    SELECT id, sender_email, message_text, sent_at, is_read
                    FROM messages
                    WHERE ((sender_email=? AND receiver_email=?) OR (sender_email=? AND receiver_email=?))
                    AND sent_at > ? AND sent_at > ?
                    ORDER BY sent_at ASC
                """, (email, friend_email, friend_email, email, last_timestamp, cleared_at))
            else:
                cur.execute("""
                    SELECT id, sender_email, message_text, sent_at, is_read
                    FROM messages
                    WHERE (sender_email=? AND receiver_email=? OR sender_email=? AND receiver_email=?)
                    AND sent_at > ?
                    ORDER BY sent_at ASC
                """, (email, friend_email, friend_email, email, last_timestamp))
        else:
            # Get all messages after clear timestamp
            if cleared_at:
                cur.execute("""
                    SELECT id, sender_email, message_text, sent_at, is_read
                    FROM messages
                    WHERE ((sender_email=? AND receiver_email=?) OR (sender_email=? AND receiver_email=?))
                    AND sent_at > ?
                    ORDER BY sent_at ASC
                """, (email, friend_email, friend_email, email, cleared_at))
            else:
                cur.execute("""
                    SELECT id, sender_email, message_text, sent_at, is_read
                    FROM messages
                    WHERE (sender_email=? AND receiver_email=?) 
                    OR (sender_email=? AND receiver_email=?)
                    ORDER BY sent_at ASC
                """, (email, friend_email, friend_email, email))

        messages = cur.fetchall()
        
        # Mark messages from friend as read
        for msg_id, sender_email, message_text, sent_at, is_read in messages:
            if sender_email == friend_email and is_read == 0:
                cur.execute("""
                    UPDATE messages SET is_read = 1 WHERE id = ?
                """, (msg_id,))
        
        conn.commit()

        # Format messages - update is_read status for marked messages
        formatted_messages = []
        for msg_id, sender_email, message_text, sent_at, is_read in messages:
            # Parse timestamp and format it
            try:
                from datetime import datetime
                msg_time = datetime.fromisoformat(sent_at).strftime("%I:%M %p")
            except:
                msg_time = sent_at

            # Update is_read if this is a message from friend that we just marked as read
            if sender_email == friend_email and is_read == 0:
                is_read = 1

            formatted_messages.append({
                "id": msg_id,
                "sender_email": sender_email,
                "text": message_text,
                "time": msg_time,
                "sent_at": sent_at,
                "type": "sent" if sender_email == email else "received",
                "is_read": is_read
            })

        conn.close()
        return JSONResponse({"messages": formatted_messages, "last_timestamp": messages[-1][3] if messages else None})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)


@app.post("/user/api/clear-messages")
def clear_messages(user_email: str = Form(...), friend_email: str = Form(...)):
    """Clear chat history for the user (messages remain for the other user)"""
    try:
        conn = get_db()
        cur = conn.cursor()

        # Insert or update the clear history record for this user
        cur.execute("""
            INSERT INTO chat_clear_history (user_email, friend_email, cleared_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_email, friend_email) DO UPDATE SET cleared_at = CURRENT_TIMESTAMP
        """, (user_email, friend_email))

        conn.commit()
        conn.close()

        return JSONResponse({"status": "success", "message": "Chat cleared for you"})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)


@app.get("/user/api/unread-count")
def get_unread_count(email: str, friend_email: str):
    """Get unread message count from a friend"""
    try:
        conn = get_db()
        cur = conn.cursor()
        
        # Count unread messages from this friend
        cur.execute("""
            SELECT COUNT(*) FROM messages
            WHERE sender_email = ? AND receiver_email = ? AND is_read = 0
        """, (friend_email, email))
        
        unread_count = cur.fetchone()[0]
        conn.close()
        
        return JSONResponse({"unread_count": unread_count})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)


@app.get("/user/api/all-unread-counts")
def get_all_unread_counts(email: str):
    """Get unread message counts from all friends"""
    try:
        conn = get_db()
        cur = conn.cursor()
        
        # Get accepted friends list
        cur.execute("""
            SELECT CASE 
                WHEN requester_email = ? THEN receiver_email 
                ELSE requester_email 
            END as friend_email
            FROM friendships
            WHERE (requester_email = ? OR receiver_email = ?) AND status = 'accepted'
        """, (email, email, email))
        
        friends = cur.fetchall()
        
        unread_data = {}
        for (friend_email,) in friends:
            cur.execute("""
                SELECT COUNT(*) FROM messages
                WHERE sender_email = ? AND receiver_email = ? AND is_read = 0
            """, (friend_email, email))
            
            unread_count = cur.fetchone()[0]
            if unread_count > 0:
                unread_data[friend_email] = unread_count
        
        conn.close()
        return JSONResponse({"unread_counts": unread_data})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)


@app.get("/user/tutorials", response_class=HTMLResponse)
def user_tutorials(email: str):
    user = get_user_by_email(email)
    if not user:
        return RedirectResponse("/user/login")

    user_id, name, email, level, xp, challenges, rank = user
    
    # Log visit to tutorials
    log_attempt(user_id, "visited", 0, "Tutorials List", "tutorials_list")

    html = open(os.path.join(FRONTEND_DIR, "tutorials.html"), encoding="utf-8").read()

    return HTMLResponse(
        html.replace("{{USERNAME}}", name)
            .replace("{{EMAIL}}", email)
            .replace("{{LEVEL}}", str(level))
            .replace("{{XP}}", str(xp))
            .replace("{{CHALLENGES}}", str(challenges))
            .replace("{{RANK}}", str(rank))
    )

# ================== USER: TUTORIAL VIEWER ==================
@app.get("/user/tutorial/{tutorial_id}", response_class=HTMLResponse)
def user_tutorial_viewer(tutorial_id: int, email: str):
    user = get_user_by_email(email)
    if not user:
        return RedirectResponse("/user/login")

    user_id = user[0]
    
    # Get tutorial name for logging
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT title FROM tutorials WHERE id=?", (tutorial_id,))
    result = cur.fetchone()
    tutorial_name = result[0] if result else f"Tutorial {tutorial_id}"
    conn.close()
    
    # Log visit to this tutorial
    log_attempt(user_id, "viewed", tutorial_id, tutorial_name, "tutorial")

    html = open(os.path.join(FRONTEND_DIR, "user_tutorial_view.html"), encoding="utf-8").read()
    return HTMLResponse(html)

# ================== USER: GET PUBLISHED TUTORIALS (API) ==================
@app.get("/user/tutorials/api/published")
def get_published_tutorials():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    try:
        cur.execute("""
            SELECT id, title, category, difficulty, estimated_time, intro, points
            FROM tutorials
            WHERE is_published = 1
            ORDER BY id ASC
        """)
        
        tutorials = []
        for row in cur.fetchall():
            tutorials.append({
                "id": row[0],
                "title": row[1],
                "category": row[2],
                "difficulty": row[3],
                "estimated_time": row[4],
                "intro": row[5],
                "points": row[6]
            })
        
        conn.close()
        return JSONResponse(tutorials)
    except Exception as e:
        conn.close()
        return JSONResponse({"error": str(e)}, status_code=500)

# ================== USER: SAVE TUTORIAL (API) ==================
@app.post("/user/tutorials/{tutorial_id}/save")
def save_tutorial(tutorial_id: int, email: str):
    user = get_user_by_email(email)
    if not user:
        return JSONResponse({"error": "User not found"}, status_code=404)
    
    user_id = user[0]
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    try:
        cur.execute("""
            INSERT OR IGNORE INTO saved_tutorials (user_id, tutorial_id, saved_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        """, (user_id, tutorial_id))
        
        conn.commit()
        conn.close()
        return JSONResponse({"success": True, "message": "Tutorial saved"})
    except Exception as e:
        conn.close()
        return JSONResponse({"error": str(e)}, status_code=500)

# ================== USER: REMOVE SAVED TUTORIAL (API) ==================
@app.delete("/user/tutorials/{tutorial_id}/unsave")
def unsave_tutorial(tutorial_id: int, email: str):
    user = get_user_by_email(email)
    if not user:
        return JSONResponse({"error": "User not found"}, status_code=404)
    
    user_id = user[0]
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    try:
        cur.execute("""
            DELETE FROM saved_tutorials
            WHERE user_id = ? AND tutorial_id = ?
        """, (user_id, tutorial_id))
        
        conn.commit()
        conn.close()
        return JSONResponse({"success": True, "message": "Tutorial removed from saved"})
    except Exception as e:
        conn.close()
        return JSONResponse({"error": str(e)}, status_code=500)

# ================== USER: CHECK IF TUTORIAL IS SAVED (API) ==================
@app.get("/user/tutorials/{tutorial_id}/is-saved")
def is_tutorial_saved(tutorial_id: int, email: str):
    user = get_user_by_email(email)
    if not user:
        return JSONResponse({"saved": False})
    
    user_id = user[0]
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    try:
        cur.execute("""
            SELECT 1 FROM saved_tutorials
            WHERE user_id = ? AND tutorial_id = ?
        """, (user_id, tutorial_id))
        
        result = cur.fetchone()
        conn.close()
        return JSONResponse({"saved": result is not None})
    except Exception as e:
        conn.close()
        return JSONResponse({"saved": False, "error": str(e)})

# ================== USER: GET SAVED TUTORIALS (API) ==================
@app.get("/user/tutorials/api/saved")
def get_saved_tutorials(email: str):
    user = get_user_by_email(email)
    if not user:
        return JSONResponse({"error": "User not found"}, status_code=404)
    
    user_id = user[0]
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    try:
        cur.execute("""
            SELECT t.id, t.title, t.category, t.difficulty, t.estimated_time, t.intro, t.points
            FROM tutorials t
            INNER JOIN saved_tutorials st ON t.id = st.tutorial_id
            WHERE st.user_id = ? AND t.is_published = 1
            ORDER BY st.saved_at DESC
        """, (user_id,))
        
        tutorials = []
        for row in cur.fetchall():
            tutorials.append({
                "id": row[0],
                "title": row[1],
                "category": row[2],
                "difficulty": row[3],
                "estimated_time": row[4],
                "intro": row[5],
                "points": row[6]
            })
        
        conn.close()
        return JSONResponse(tutorials)
    except Exception as e:
        conn.close()
        return JSONResponse({"error": str(e)}, status_code=500)

# ================== USER: SAVE TUTORIAL PROGRESS (API) ==================
@app.post("/user/tutorials/{tutorial_id}/progress")
def save_tutorial_progress(tutorial_id: int, payload: dict = Body(...)):
    # Extract data from request body
    email = payload.get("email")
    current_class_index = payload.get("current_class_index", 0)
    progress_percentage = payload.get("progress_percentage", 0)
    
    user = get_user_by_email(email)
    if not user:
        return JSONResponse({"error": "User not found"}, status_code=404)
    
    user_id = user[0]  # Get user_id for level update
    username = user[1].strip()  # Get username from user tuple and remove whitespace
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    try:
        print(f"DEBUG: Saving progress - tutorial_id={tutorial_id}, username={username}, current_class_index={current_class_index}, progress_percentage={progress_percentage}")
        
        # ONLY use admin-provided progress_percentage. Never calculate it.
        # Progress must always come from admin-set values for each class.
        progress = progress_percentage
        print(f"DEBUG: Using admin-provided progress_percentage: {progress}")
        
        # Check if tutorial was already completed (100%) BEFORE update
        cur.execute("""
            SELECT progress, completed FROM tutorial_progress
            WHERE username = ? AND tutorial_id = ?
        """, (username, tutorial_id))
        
        result = cur.fetchone()
        was_already_completed = (result[1] == 1) if result else False
        print(f"DEBUG: was_already_completed={was_already_completed}, result={result}")
        
        cur.execute("""
            INSERT INTO tutorial_progress (username, tutorial_id, current_class_index, progress, completed, completed_at, last_accessed)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(username, tutorial_id) DO UPDATE SET
            current_class_index = ?,
            progress = ?,
            completed = CASE WHEN ? = 100 THEN 1 ELSE completed END,
            completed_at = CASE WHEN ? = 100 THEN CURRENT_TIMESTAMP ELSE completed_at END,
            last_accessed = CURRENT_TIMESTAMP
        """, (username, tutorial_id, current_class_index, progress, 1 if progress == 100 else 0, None if progress < 100 else 'CURRENT_TIMESTAMP', current_class_index, progress, progress, progress))
        
        # If tutorial is NOW being completed (progress=100) and was NOT completed before, increment user level and award points
        if progress == 100 and not was_already_completed:
            # Get tutorial points
            cur.execute("""
                SELECT points FROM tutorials WHERE id = ?
            """, (tutorial_id,))
            tutorial_points = cur.fetchone()
            points_to_award = tutorial_points[0] if tutorial_points and tutorial_points[0] else 0
            
            print(f"DEBUG: Awarding {points_to_award} points to user {username}")
            
            # Update user XP and level
            cur.execute("""
                UPDATE users
                SET level = level + 1, xp = xp + ?
                WHERE id = ?
            """, (points_to_award, user_id,))
            print(f"DEBUG: Level incremented for user {username} (from completion of tutorial {tutorial_id}). Awarded {points_to_award} points. New level progression applied.")
            
            # Sync ranking table with updated user data
            cur.execute("""
                INSERT OR REPLACE INTO ranking (user_id, score, level, challenges_solved)
                SELECT id, xp, level, challenges_solved
                FROM users
                WHERE id = ?
            """, (user_id,))
            
            # Recalculate and update global ranks for all users
            cur.execute("""
                SELECT id
                FROM users
                ORDER BY xp DESC, level DESC
            """)
            users = cur.fetchall()
            for rank, (uid,) in enumerate(users, start=1):
                cur.execute(
                    "UPDATE users SET global_rank=? WHERE id=?",
                    (rank, uid)
                )
            
            print(f"DEBUG: Ranking table and global ranks updated for tutorial completion.")
        elif progress == 100 and was_already_completed:
            print(f"DEBUG: Tutorial {tutorial_id} already completed before, no level increase or points awarded.")
        
        conn.commit()
        
        conn.close()
        print(f"DEBUG: Progress saved successfully - progress={progress}")
        return JSONResponse({"success": True, "message": "Progress saved", "progress": progress})
    except Exception as e:
        conn.close()
        print(f"DEBUG: Error saving progress: {str(e)}")
        return JSONResponse({"error": str(e)}, status_code=500)

# ================== USER: GET TUTORIAL PROGRESS (API) ==================
@app.get("/user/tutorials/{tutorial_id}/progress")
def get_tutorial_progress(tutorial_id: int, email: str):
    user = get_user_by_email(email)
    if not user:
        return JSONResponse({"current_class_index": 0})
    
    username = user[1].strip()  # Get username from user tuple and remove whitespace
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    try:
        cur.execute("""
            SELECT current_class_index, progress, completed
            FROM tutorial_progress
            WHERE username = ? AND tutorial_id = ?
        """, (username, tutorial_id))
        
        result = cur.fetchone()
        conn.close()
        
        if result:
            return JSONResponse({
                "current_class_index": result[0],
                "progress": result[1],
                "completed": result[2]
            })
        else:
            return JSONResponse({
                "current_class_index": 0,
                "progress": 0,
                "completed": 0
            })
    except Exception as e:
        conn.close()
        return JSONResponse({"current_class_index": 0, "error": str(e)})

# ================== USER: SUBMIT TUTORIAL RATING ==================
@app.post("/user/tutorials/{tutorial_id}/rate")
def rate_tutorial(tutorial_id: int, email: str, rating: int):
    """Rate a tutorial"""
    user = get_user_by_email(email)
    if not user:
        return JSONResponse({"error": "User not found"}, status_code=404)
    
    user_id = user[0]
    
    if rating < 1 or rating > 5:
        return JSONResponse({"error": "Rating must be between 1 and 5"}, status_code=400)
    
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    try:
        cur.execute("""
            INSERT INTO tutorial_ratings (user_id, tutorial_id, rating, created_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, tutorial_id) DO UPDATE SET rating=?, created_at=CURRENT_TIMESTAMP
        """, (user_id, tutorial_id, rating, rating))
        
        # Recalculate average rating for this tutorial
        cur.execute("""
            SELECT AVG(rating), COUNT(*) FROM tutorial_ratings WHERE tutorial_id=?
        """, (tutorial_id,))
        result = cur.fetchone()
        avg_rating = result[0] if result[0] else 0
        rating_count = result[1] if result[1] else 0
        
        cur.execute("""
            UPDATE tutorials SET average_rating=?, rating_count=? WHERE id=?
        """, (avg_rating, rating_count, tutorial_id))
        
        conn.commit()
        conn.close()
        return JSONResponse({"success": True, "message": f"Rating submitted! Average: {avg_rating:.1f}"})
    except Exception as e:
        conn.close()
        return JSONResponse({"error": str(e)}, status_code=500)


# ================== USER: GET TUTORIAL RATING ==================
@app.get("/user/tutorials/{tutorial_id}/rating")
def get_tutorial_rating(tutorial_id: int, email: str):
    """Get tutorial rating info"""
    user = get_user_by_email(email)
    if not user:
        return JSONResponse({"average_rating": 0, "user_rating": 0})
    
    user_id = user[0]
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    try:
        # Get user's rating
        cur.execute("""
            SELECT rating FROM tutorial_ratings
            WHERE user_id=? AND tutorial_id=?
        """, (user_id, tutorial_id))
        user_rating_result = cur.fetchone()
        user_rating = user_rating_result[0] if user_rating_result else 0
        
        # Get average rating
        cur.execute("""
            SELECT t.average_rating, t.rating_count FROM tutorials t WHERE id=?
        """, (tutorial_id,))
        tutorial_result = cur.fetchone()
        average_rating = tutorial_result[0] if tutorial_result and tutorial_result[0] else 0
        rating_count = tutorial_result[1] if tutorial_result and tutorial_result[1] else 0
        
        conn.close()
        return JSONResponse({
            "average_rating": average_rating,
            "rating_count": rating_count,
            "user_rating": user_rating
        })
    except Exception as e:
        conn.close()
        return JSONResponse({"average_rating": 0, "user_rating": 0, "error": str(e)})


# ================== SYNC TUTORIAL POINTS (ADMIN) ==================
@app.post("/admin/api/sync-tutorial-points")
def sync_tutorial_points():
    """Sync all completed tutorials and ensure points are properly awarded"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    try:
        # Find all users with completed tutorials but mismatched XP
        cur.execute("""
            SELECT DISTINCT tp.username, tp.tutorial_id, t.points
            FROM tutorial_progress tp
            LEFT JOIN tutorials t ON tp.tutorial_id = t.id
            WHERE tp.completed = 1 AND t.points > 0
        """)
        
        completed_tutorials = cur.fetchall()
        sync_count = 0
        
        for username, tutorial_id, points in completed_tutorials:
            # Get user
            cur.execute("SELECT id, xp FROM users WHERE name=?", (username,))
            user_result = cur.fetchone()
            
            if user_result:
                user_id, current_xp = user_result
                
                # Check if tutorial points are in user's XP
                cur.execute("""
                    SELECT COUNT(*) FROM tutorial_progress
                    WHERE username=? AND completed=1
                """, (username,))
                completed_count = cur.fetchone()[0]
                
                # Update user's XP to include all completed tutorial points
                cur.execute("""
                    UPDATE users
                    SET xp = xp + ?
                    WHERE id=? AND xp < ?
                """, (points, user_id, current_xp + points))
                
                if cur.rowcount > 0:
                    sync_count += 1
                    print(f"[SYNC] Updated user {username}: +{points} points for tutorial {tutorial_id}")
        
        conn.commit()
        conn.close()
        
        return JSONResponse({
            "success": True,
            "synced_tutorials": sync_count,
            "message": f"Successfully synced {sync_count} tutorial completions"
        })
    except Exception as e:
        conn.close()
        print(f"[SYNC ERROR] {str(e)}")
        return JSONResponse({"error": str(e)}, status_code=500)


# ================== GET USER SCORE BREAKDOWN (API) ==================
@app.get("/api/user/score-breakdown")
def get_user_score_breakdown(email: str):
    """Get detailed breakdown of user's score from tutorials and challenges"""
    user = get_user_by_email(email)
    if not user:
        return JSONResponse({"error": "User not found"}, status_code=404)
    
    user_id, name, email_addr, level, xp, challenges_solved, rank = user
    
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    try:
        # Get all completed tutorials with their points
        cur.execute("""
            SELECT tp.tutorial_id, t.title, t.points, tp.completed_at
            FROM tutorial_progress tp
            LEFT JOIN tutorials t ON tp.tutorial_id = t.id
            WHERE tp.username=? AND tp.completed=1
            ORDER BY tp.completed_at DESC
        """, (name,))
        
        completed_tutorials = []
        tutorial_total_points = 0
        
        for tutorial_id, title, points, completed_at in cur.fetchall():
            points = points or 0
            tutorial_total_points += points
            completed_tutorials.append({
                "tutorial_id": tutorial_id,
                "title": title or f"Tutorial {tutorial_id}",
                "points": points,
                "completed_at": completed_at
            })
        
        # Get challenge points from user's XP (assuming challenges award XP points)
        challenge_points = xp - tutorial_total_points
        if challenge_points < 0:
            challenge_points = xp  # If calculation is negative, all points are from challenges
        
        conn.close()
        
        return JSONResponse({
            "success": True,
            "user": name,
            "challenge_xp": xp,
            "tutorial_points": tutorial_total_points,
            "total_score": xp,
            "completed_tutorials_count": len(completed_tutorials),
            "completed_tutorials": completed_tutorials,
            "level": level,
            "rank": rank
        })
    except Exception as e:
        conn.close()
        return JSONResponse({"error": str(e)}, status_code=500)



# ================== USER: SUBMIT TUTORIAL RATING ==================
@app.post("/user/tutorials/{tutorial_id}/rate")
def submit_tutorial_rating(tutorial_id: int, email: str, rating: int):
    if rating < 1 or rating > 5:
        return JSONResponse({"error": "Rating must be between 1 and 5"}, status_code=400)
    
    user = get_user_by_email(email)
    if not user:
        return JSONResponse({"error": "User not found"}, status_code=404)
    
    user_id = user[0]
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    try:
        # Insert or update the rating
        cur.execute("""
            INSERT INTO tutorial_ratings (user_id, tutorial_id, rating, created_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, tutorial_id) DO UPDATE SET
            rating = ?,
            created_at = CURRENT_TIMESTAMP
        """, (user_id, tutorial_id, rating, rating))
        
        # Recalculate average rating and rating count
        cur.execute("""
            SELECT AVG(rating), COUNT(*) FROM tutorial_ratings WHERE tutorial_id = ?
        """, (tutorial_id,))
        
        avg_rating, count = cur.fetchone()
        avg_rating = round(avg_rating, 2) if avg_rating else 0
        
        # Update the tutorial with new rating stats
        cur.execute("""
            UPDATE tutorials SET average_rating = ?, rating_count = ? WHERE id = ?
        """, (avg_rating, count, tutorial_id))
        
        conn.commit()
        conn.close()
        
        return JSONResponse({
            "success": True,
            "message": "Rating submitted",
            "average_rating": avg_rating,
            "rating_count": count
        })
    except Exception as e:
        conn.close()
        return JSONResponse({"error": str(e)}, status_code=500)

# ================== USER: GET TUTORIAL RATINGS ==================
@app.get("/user/tutorials/{tutorial_id}/rating")
def get_tutorial_rating(tutorial_id: int, email: str = ""):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    try:
        # Get tutorial rating stats
        cur.execute("""
            SELECT average_rating, rating_count FROM tutorials WHERE id = ?
        """, (tutorial_id,))
        
        result = cur.fetchone()
        if not result:
            conn.close()
            return JSONResponse({"error": "Tutorial not found"}, status_code=404)
        
        average_rating, rating_count = result
        user_rating = 0
        
        # If user provided, get their rating
        if email:
            user = get_user_by_email(email)
            if user:
                user_id = user[0]
                cur.execute("""
                    SELECT rating FROM tutorial_ratings 
                    WHERE user_id = ? AND tutorial_id = ?
                """, (user_id, tutorial_id))
                
                user_result = cur.fetchone()
                if user_result:
                    user_rating = user_result[0]
        
        # Get completion rate
        cur.execute("""
            SELECT COUNT(*) FROM tutorial_progress 
            WHERE tutorial_id = ? AND completed = 1
        """, (tutorial_id,))
        completed_count = cur.fetchone()[0]
        
        cur.execute("""
            SELECT COUNT(*) FROM tutorial_progress 
            WHERE tutorial_id = ?
        """, (tutorial_id,))
        total_count = cur.fetchone()[0]
        
        completion_rate = 0
        if total_count > 0:
            completion_rate = int((completed_count / total_count) * 100)
        
        conn.close()
        
        return JSONResponse({
            "average_rating": average_rating,
            "rating_count": rating_count,
            "user_rating": user_rating,
            "completion_rate": completion_rate,
            "completed_count": completed_count,
            "total_count": total_count
        })
    except Exception as e:
        conn.close()
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/user/tutorials/cyber-security", response_class=HTMLResponse)
def cyber_security_tutorial(email: str):
    user = get_user_by_email(email)
    if not user:
        return RedirectResponse("/user/login")

    user_id, name, email, level, xp, challenges, rank = user

    html = open(
        os.path.join(FRONTEND_DIR, "tuto1_intro.html"),
        encoding="utf-8"
    ).read()

    return HTMLResponse(
        html.replace("{{USERNAME}}", name)
            .replace("{{EMAIL}}", email)
            .replace("{{LEVEL}}", str(level))
    )

#user progress
@app.get("/user/progress", response_class=HTMLResponse)
def user_progress(email: str):
    user = get_user_by_email(email)
    if not user:
        return RedirectResponse("/user/login")

    user_id, name, email, level, xp, challenges, rank = user

    # Total score is already in xp (tutorial points are included in xp)
    total_score = xp
    
    html = open(os.path.join(FRONTEND_DIR, "progress_graph.html"), encoding="utf-8").read()

    return HTMLResponse(
        html.replace("{{USERNAME}}", name)
            .replace("{{EMAIL}}", email)
            .replace("{{LEVEL}}", str(level))
            .replace("{{XP}}", str(total_score))
            .replace("{{CHALLENGES}}", str(challenges))
            .replace("{{RANK}}", str(rank))
    )

#user saved challenges
@app.get("/user/saved-challenges", response_class=HTMLResponse)
def user_saved_challenges(email: str):
    user = get_user_by_email(email)
    if not user:
        return RedirectResponse("/user/login")

    name, email, level, *_ = user

    html = open(
        os.path.join(FRONTEND_DIR, "user_saved_challenges.html"),
        encoding="utf-8"
    ).read()

    return HTMLResponse(
        html.replace("{{EMAIL}}", email)
            .replace("{{USERNAME}}", name)
            .replace("{{LEVEL}}", str(level))
    )


@app.get("/user/challenge/fakebank", response_class=HTMLResponse)
def fakebank_lab(email: str):
    user = get_user_by_email(email)
    if not user:
        return RedirectResponse("/user/login")

    user_id, name, email, level, xp, challenges, rank = user
    
    # Log visit to fakebank challenge
    log_attempt(user_id, "started", 1, "FakeBank Challenge", "challenge")

    html = open(
        os.path.join(FRONTEND_DIR, "fakebank_lab.html"),
        encoding="utf-8"
    ).read()

    return HTMLResponse(
        html.replace("{{USERNAME}}", name)
            .replace("{{EMAIL}}", email)
            .replace("{{LEVEL}}", str(level))
    )

@app.get("/user/challenge/fakebank_lab", response_class=HTMLResponse)
def fakebank_simulation(email: str):
    user = get_user_by_email(email)
    if not user:
        return RedirectResponse("/user/login")

    user_id, name, email, level, *_ = user
    
    # Log visit
    log_attempt(user_id, "started", 1, "FakeBank Simulation", "challenge")

    html = open(
        os.path.join(FRONTEND_DIR, "challenge_simulation.html"),
        encoding="utf-8"
    ).read()

    return HTMLResponse(
        html.replace("{{USERNAME}}", name)
            .replace("{{EMAIL}}", email)
            .replace("{{LEVEL}}", str(level))
    )

@app.get("/user/challenge/fakebank/start", response_class=HTMLResponse)
def fakebank_split_view(email: str):
    user = get_user_by_email(email)
    if not user:
        return RedirectResponse("/user/login")

    user_id, name, email, level, *_ = user
    
    # Log visit
    log_attempt(user_id, "started", 1, "FakeBank Split View", "challenge")

    html = open(
        os.path.join(FRONTEND_DIR, "fakebank_split.html"),
        encoding="utf-8"
    ).read()

    return HTMLResponse(
        html.replace("{{USERNAME}}", name)
            .replace("{{EMAIL}}", email)
            .replace("{{LEVEL}}", str(level))
    )


#admin sidebar
@app.get("/admin/users", response_class=HTMLResponse)
def admin_users():
    return open(os.path.join(FRONTEND_DIR, "admin_users.html"), encoding="utf-8").read()


@app.get("/admin/challenges", response_class=HTMLResponse)
def admin_challenges():
    return open(os.path.join(FRONTEND_DIR, "admin_challenges.html"), encoding="utf-8").read()

@app.get("/admin/docker-images")
def get_available_docker_images():
    """Get list of available Docker images with their associated flags from the database"""
    # 1. Get images from Docker Desktop
    result = DockerManager.list_images()
    desktop_images = result.get('images', []) if result['success'] else []
    
    # 2. Get images and flags from database
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT docker_image, flag FROM challenges WHERE docker_image IS NOT NULL")
        db_data = {row[0]: row[1] for row in cur.fetchall()}
        conn.close()
    except Exception as e:
        print(f"[ERROR] Database error in get_available_docker_images: {e}")
        db_data = {}
    
    # 3. Merge data - combine Docker Desktop images and database images
    all_images = sorted(list(set(desktop_images) | set(db_data.keys())))
    
    image_list = []
    for img in all_images:
        # Avoid including placeholder images in the main audit list if possible
        if img == "placeholder_image":
            continue
            
        image_list.append({
            "image": img,
            "flag": db_data.get(img, "No flag available")
        })
        
    return JSONResponse({
        "success": True,
        "images": image_list,
        "count": len(image_list)
    })


@app.post("/admin/challenges/create")
def create_challenge_admin(body: dict = Body(...)):
    """Create a new challenge from JSON payload"""
    conn = get_db()
    cur = conn.cursor()

    title = body.get('title', '').strip()
    category = body.get('category', 'Web')
    difficulty = body.get('difficulty', 'Easy')
    points = body.get('points', 100)
    description = body.get('description', '').strip()
    flag = body.get('flag', '').strip()
    docker_image = body.get('docker_image', '').strip()

    if not title or not description or not flag:
        return JSONResponse({"success": False, "error": "Missing required fields"}, status_code=400)
    
    if not docker_image:
        return JSONResponse({"success": False, "error": "Docker image is required"}, status_code=400)

    try:
        cur.execute("""
            INSERT INTO challenges (title, category, difficulty, points, description, flag, docker_image, is_published)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0)
        """, (title, category, difficulty, points, description, flag, docker_image))

        conn.commit()
        challenge_id = cur.lastrowid
        conn.close()

        return JSONResponse({"success": True, "id": challenge_id, "message": "Challenge saved successfully"})
    except Exception as e:
        conn.close()
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

@app.post("/admin/challenges/{challenge_id}/update")
def update_challenge_admin(challenge_id: int, body: dict = Body(...)):
    """Update an existing challenge"""
    conn = get_db()
    cur = conn.cursor()

    title = body.get('title', '').strip()
    category = body.get('category', 'Web')
    difficulty = body.get('difficulty', 'Easy')
    points = body.get('points', 100)
    description = body.get('description', '').strip()
    flag = body.get('flag', '').strip()
    docker_image = body.get('docker_image', '').strip()

    if not title or not description or not flag:
        conn.close()
        return JSONResponse({"success": False, "error": "Missing required fields"}, status_code=400)
    
    if not docker_image:
        conn.close()
        return JSONResponse({"success": False, "error": "Docker image is required"}, status_code=400)

    try:
        cur.execute("""
            UPDATE challenges 
            SET title = ?, category = ?, difficulty = ?, points = ?, description = ?, flag = ?, docker_image = ?
            WHERE id = ?
        """, (title, category, difficulty, points, description, flag, docker_image, challenge_id))

        if cur.rowcount == 0:
            conn.close()
            return JSONResponse({"success": False, "error": "Challenge not found"}, status_code=404)

        conn.commit()
        conn.close()

        return JSONResponse({"success": True, "id": challenge_id, "message": "Challenge updated successfully"})
    except Exception as e:
        conn.close()
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

@app.get("/admin/api/challenges")
def get_admin_challenges_api():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, title, category, difficulty, points, description, flag, docker_image, is_published, created_at
        FROM challenges
        ORDER BY created_at ASC
    """)
    
    rows = cur.fetchall()
    conn.close()

    challenges = []
    for r in rows:
        challenges.append({
            "id": r[0],
            "title": r[1],
            "category": r[2],
            "difficulty": r[3],
            "points": r[4],
            "description": r[5],
            "flag": r[6],
            "docker_image": r[7],
            "is_published": r[8],
            "created_at": r[9]
        })

    return JSONResponse(
        challenges,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}
    )

@app.get("/admin/api/challenges/{challenge_id}")
def get_admin_challenge_by_id(challenge_id: int):
    """Get a single challenge by ID"""
    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT id, title, category, difficulty, points, description, flag, docker_image, is_published, created_at
            FROM challenges
            WHERE id = ?
        """, (challenge_id,))
        
        row = cur.fetchone()
        conn.close()

        if not row:
            print(f"[DEBUG] Challenge with ID {challenge_id} not found in database", flush=True)
            return JSONResponse({"success": False, "error": "Challenge not found"})

        challenge = {
            "success": True,
            "id": row[0],
            "title": row[1],
            "category": row[2],
            "difficulty": row[3],
            "points": row[4],
            "description": row[5],
            "flag": row[6],
            "docker_image": row[7],
            "is_published": row[8],
            "created_at": row[9]
        }

        print(f"[DEBUG] Successfully loaded challenge {challenge_id}: {row[1]}", flush=True)
        return JSONResponse(challenge)
    except Exception as e:
        conn.close()
        print(f"[ERROR] Failed to load challenge {challenge_id}: {str(e)}", flush=True)
        return JSONResponse({"success": False, "error": str(e)})

@app.get("/admin/api/challenge-solves")
def get_challenge_solves():
    """Get all challenges with their completion counts"""
    try:
        conn = get_db()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT id, challenge_id, title, category, count
            FROM challenge_solves
            ORDER BY category ASC, title ASC
        """)
        
        rows = cur.fetchall()
        conn.close()
        
        solves = []
        for row in rows:
            solves.append({
                "id": row[0],
                "challenge_id": row[1],
                "title": row[2],
                "category": row[3] or "Uncategorized",
                "count": row[4]
            })
        
        return JSONResponse({"success": True, "data": solves})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

@app.get("/admin/api/challenge-solves/by-category")
def get_challenge_solves_by_category():
    """Get challenge completion statistics grouped by category"""
    try:
        conn = get_db()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT 
                category,
                COUNT(*) as total_challenges,
                SUM(count) as total_completions,
                ROUND(AVG(count), 1) as avg_completions
            FROM challenge_solves
            GROUP BY category
            ORDER BY category ASC
        """)
        
        rows = cur.fetchall()
        conn.close()
        
        categories = []
        for row in rows:
            categories.append({
                "category": row[0] or "Uncategorized",
                "total_challenges": row[1],
                "total_completions": row[2] or 0,
                "avg_completions_per_challenge": row[3] or 0
            })
        
        return JSONResponse({"success": True, "data": categories})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

@app.post("/admin/api/challenge-solves/refresh")
def refresh_challenge_solves():
    """Refresh the challenge_solves table with current completion data"""
    try:
        conn = get_db()
        cur = conn.cursor()
        
        # Get all current challenges
        cur.execute("SELECT id, title, category FROM challenges ORDER BY id")
        challenges = cur.fetchall()
        
        # Clear existing data
        cur.execute("DELETE FROM challenge_solves")
        
        # Repopulate with current data
        for challenge in challenges:
            challenge_id, title, category = challenge
            cur.execute("""
                SELECT COUNT(DISTINCT user_id)
                FROM user_challenges
                WHERE challenge_id = ? AND progress >= 100
            """, (challenge_id,))
            count = cur.fetchone()[0] or 0
            
            cur.execute("""
                INSERT INTO challenge_solves (challenge_id, title, category, count)
                VALUES (?, ?, ?, ?)
            """, (challenge_id, title, category or "Uncategorized", count))
        
        conn.commit()
        conn.close()
        
        return JSONResponse({"success": True, "message": "Challenge solves data refreshed successfully"})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

@app.post("/admin/challenges/{challenge_id}/publish")
def publish_challenge(challenge_id: int):
    """Publish a challenge to make it visible to users"""
    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute("UPDATE challenges SET is_published = 1 WHERE id = ?", (challenge_id,))
        
        if cur.rowcount == 0:
            conn.close()
            return JSONResponse({"success": False, "error": "Challenge not found"}, status_code=404)
        
        conn.commit()
        conn.close()
        
        return JSONResponse({"success": True, "message": "Challenge published successfully"})
    except Exception as e:
        conn.close()
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

@app.post("/admin/challenges/{challenge_id}/unpublish")
def unpublish_challenge(challenge_id: int):
    """Unpublish a challenge to hide it from users"""
    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute("UPDATE challenges SET is_published = 0 WHERE id = ?", (challenge_id,))
        
        if cur.rowcount == 0:
            conn.close()
            return JSONResponse({"success": False, "error": "Challenge not found"}, status_code=404)
        
        conn.commit()
        conn.close()
        
        return JSONResponse({"success": True, "message": "Challenge unpublished successfully"})
    except Exception as e:
        conn.close()
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

@app.post("/admin/challenges/{challenge_id}/delete")
def delete_challenge(challenge_id: int):
    """Delete a challenge"""
    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute("DELETE FROM challenges WHERE id = ?", (challenge_id,))
        
        if cur.rowcount == 0:
            conn.close()
            return JSONResponse({"success": False, "error": "Challenge not found"}, status_code=404)
        
        conn.commit()
        conn.close()
        
        print(f"[INFO] Challenge {challenge_id} deleted successfully", flush=True)
        return JSONResponse({"success": True, "message": "Challenge deleted successfully"})
    except Exception as e:
        conn.close()
        print(f"[ERROR] Failed to delete challenge {challenge_id}: {str(e)}", flush=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

@app.post("/admin/challenges/clear-all")
def clear_all_challenges():
    """Clear all challenges from the database (ADMIN ONLY)"""
    conn = get_db()
    cur = conn.cursor()

    try:
        # Delete all challenges and related records
        cur.execute("DELETE FROM user_challenges")
        cur.execute("DELETE FROM running_containers")
        cur.execute("DELETE FROM challenges")
        
        conn.commit()
        conn.close()
        
        return JSONResponse({"success": True, "message": "All challenges have been cleared"})
    except Exception as e:
        conn.close()
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

@app.post("/admin/challenges/{challenge_id}/preview")
def preview_challenge_docker(challenge_id: int, body: dict = Body(...)):
    """Start Docker container for challenge preview"""
    print(f"[PREVIEW] Endpoint called for challenge_id={challenge_id}", flush=True)
    
    # Check if Docker is available first
    if not DOCKER_AVAILABLE:
        print(f"[PREVIEW] Docker module not available", flush=True)
        return JSONResponse({
            "success": False, 
            "error": "Docker module is not installed. Please install it with: pip install docker"
        }, status_code=500)
    
    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute(
            "SELECT id, docker_image FROM challenges WHERE id = ?",
            (challenge_id,)
        )
        challenge = cur.fetchone()
        conn.close()
        
        if not challenge:
            print(f"[PREVIEW] Challenge {challenge_id} not found", flush=True)
            return JSONResponse({"success": False, "error": "Challenge not found"}, status_code=404)
        
        docker_image = challenge[1]
        
        if not docker_image:
            print(f"[PREVIEW] Challenge {challenge_id} has no docker image", flush=True)
            return JSONResponse({
                "success": False,
                "error": "Challenge has no Docker image configured"
            }, status_code=400)
        
        if docker_image == "placeholder_image":
            print(f"[PREVIEW] Challenge {challenge_id} has placeholder docker image", flush=True)
            return JSONResponse({
                "success": False,
                "error": f"Challenge Docker image not configured. Currently set to: '{docker_image}'. Please set a valid Docker image name in the challenge settings."
            }, status_code=400)
        
        print(f"[PREVIEW] Starting container for challenge_id={challenge_id}, image={docker_image}", flush=True)
        # Build or start container
        result = DockerManager.start_container(
            docker_image,
            challenge_id,
            user_id=0,  # Admin preview
            is_preview=True
        )
        
        if result['success']:
            print(f"[PREVIEW] Container started successfully: {result['container_name']}, port={result['port']}", flush=True)
            return JSONResponse({
                "success": True,
                "container_id": result['container_id'],
                "container_name": result['container_name'],
                "port": result['port'],
                "access_url": f"http://127.0.0.1:{result['port']}",
                "message": f"Challenge preview running on http://127.0.0.1:{result['port']}"
            })
        else:
            error_msg = result.get('error', 'Failed to start container')
            print(f"[PREVIEW] Failed to start container: {error_msg}", flush=True)
            return JSONResponse({"success": False, "error": error_msg}, status_code=500)
            
    except Exception as e:
        print(f"Error previewing challenge: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

# ================== DOCKER CONTAINER MANAGEMENT ==================

@app.post("/user/challenges/{challenge_id}/start-container")
def start_challenge_container(challenge_id: int, email: str):
    """Start a Docker container for a challenge"""
    try:
        # Get user
        user = get_user_by_email(email)
        if not user:
            return JSONResponse({"success": False, "error": "User not found"}, status_code=404)
        
        user_id = user[0]
        
        # Get challenge
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id, docker_image FROM challenges WHERE id = ?", (challenge_id,))
        challenge = cur.fetchone()
        
        if not challenge:
            conn.close()
            return JSONResponse({"success": False, "error": "Challenge not found"}, status_code=404)
        
        # Check if challenge is locked
        cur.execute("""
            SELECT id FROM challenges
            WHERE is_published = 1
            ORDER BY created_at ASC
        """)
        all_challenges = [r[0] for r in cur.fetchall()]
        
        # Find challenge position
        challenge_position = None
        for pos, cid in enumerate(all_challenges):
            if cid == challenge_id:
                challenge_position = pos
                break
        
        # Check if it's locked (first challenge is always unlocked)
        if challenge_position is not None and challenge_position > 0:
            previous_challenge_id = all_challenges[challenge_position - 1]
            cur.execute("""
                SELECT progress FROM user_challenges
                WHERE user_id = ? AND challenge_id = ? AND progress >= 100
            """, (user_id, previous_challenge_id))
            
            if not cur.fetchone():
                conn.close()
                return JSONResponse({"success": False, "error": "Challenge is locked. Complete previous challenge first."}, status_code=403)
        
        docker_image = challenge[1]
        conn.close()
        
        if not docker_image:
            return JSONResponse({"success": False, "error": "Challenge has no Docker image configured"}, status_code=400)
        
        # ALWAYS clean up any old containers for this user-challenge combo first
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT container_id FROM running_containers 
            WHERE user_id = ? AND challenge_id = ?
        """, (user_id, challenge_id))
        old_containers = cur.fetchall()
        conn.close()
        
        # Stop and remove any old containers
        for (old_container_id,) in old_containers:
            print(f"[CLEANUP] Stopping old container: {old_container_id[:12]}...", flush=True)
            stop_result = DockerManager.stop_container(old_container_id)
            if stop_result.get('success'):
                print(f"[CLEANUP] Successfully stopped container {old_container_id[:12]}", flush=True)
            else:
                print(f"[CLEANUP] Could not stop container {old_container_id[:12]}: {stop_result.get('error', 'Unknown error')}", flush=True)
        
        # DELETE all old containers from database (not just mark as stopped) to avoid UNIQUE constraint conflicts
        if old_containers:
            conn = get_db()
            cur = conn.cursor()
            try:
                cur.execute("""
                    DELETE FROM running_containers 
                    WHERE user_id = ? AND challenge_id = ?
                """, (user_id, challenge_id))
                conn.commit()
                print(f"[CLEANUP] Deleted {len(old_containers)} old container records from database", flush=True)
            except sqlite3.OperationalError as e:
                print(f"[ERROR] Failed to delete old containers: {e}", flush=True)
                conn.rollback()
            conn.close()
        
        # Now start a new container
        result = DockerManager.start_container(
            docker_image,
            challenge_id,
            user_id,
            is_preview=False
        )
        
        if result['success']:
            # Save to database with retry logic for database locks
            try:
                conn = get_db()
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO running_containers (challenge_id, user_id, container_id, container_name, port_mapping, status, duration_minutes)
                    VALUES (?, ?, ?, ?, ?, 'running', 30)
                """, (challenge_id, user_id, result['container_id'], result['container_name'], str(result['port'])))
                conn.commit()
                print(f"[INFO] Successfully inserted container record for challenge {challenge_id}, user {user_id}", flush=True)
                conn.close()
            except sqlite3.IntegrityError as e:
                print(f"[ERROR] Database integrity error (likely UNIQUE constraint): {e}", flush=True)
                conn.rollback()
                conn.close()
                result = {"success": False, "error": "Container already running or database conflict. Please try again."}
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e):
                    print(f"[ERROR] Database is locked. Retrying in 100ms: {e}", flush=True)
                    import time
                    time.sleep(0.1)
                    try:
                        conn = get_db()
                        cur = conn.cursor()
                        cur.execute("""
                            INSERT INTO running_containers (challenge_id, user_id, container_id, container_name, port_mapping, status, duration_minutes)
                            VALUES (?, ?, ?, ?, ?, 'running', 30)
                        """, (challenge_id, user_id, result['container_id'], result['container_name'], str(result['port'])))
                        conn.commit()
                        conn.close()
                        print(f"[INFO] Successfully inserted container record after retry", flush=True)
                    except Exception as retry_error:
                        print(f"[ERROR] Failed to insert after retry: {retry_error}", flush=True)
                        conn.rollback()
                        conn.close()
                        result = {"success": False, "error": "Database error after retry"}
                else:
                    print(f"[ERROR] Database operational error: {e}", flush=True)
                    conn.rollback()
                    conn.close()
                    result = {"success": False, "error": str(e)}
            except Exception as e:
                print(f"[ERROR] Unexpected error saving container record: {e}", flush=True)
                try:
                    conn.rollback()
                    conn.close()
                except:
                    pass
                result = {"success": False, "error": str(e)}
        
        return JSONResponse(result)
        
    except Exception as e:
        print(f"Error starting container: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

@app.post("/user/challenges/{challenge_id}/stop-container")
def stop_challenge_container(challenge_id: int, email: str):
    """Stop a running Docker container for a challenge"""
    try:
        # Get user
        user = get_user_by_email(email)
        if not user:
            return JSONResponse({"success": False, "error": "User not found"}, status_code=404)
        
        user_id = user[0]
        
        # Get running container
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT container_id FROM running_containers 
            WHERE user_id = ? AND challenge_id = ? AND status = 'running'
        """, (user_id, challenge_id))
        result = cur.fetchone()
        
        if not result:
            conn.close()
            return JSONResponse({"success": False, "error": "No running container found"}, status_code=404)
        
        container_id = result[0]
        
        # Stop container
        docker_result = DockerManager.stop_container(container_id)
        
        if docker_result['success']:
            # Update database
            cur.execute("""
                UPDATE running_containers 
                SET status = 'stopped', stopped_at = CURRENT_TIMESTAMP
                WHERE container_id = ?
            """, (container_id,))
            conn.commit()
        
        conn.close()
        
        return JSONResponse(docker_result)
        
    except Exception as e:
        print(f"Error stopping container: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

@app.get("/user/challenges/{challenge_id}/container-status")
def get_container_status(challenge_id: int, email: str):
    """Get status of running container for a challenge"""
    try:
        # Get user
        user = get_user_by_email(email)
        if not user:
            return JSONResponse({"success": False, "error": "User not found"}, status_code=404)
        
        user_id = user[0]
        
        # Get running container
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT container_id, container_name, port_mapping, status, started_at 
            FROM running_containers 
            WHERE user_id = ? AND challenge_id = ? AND status = 'running'
        """, (user_id, challenge_id))
        result = cur.fetchone()
        conn.close()
        
        if not result:
            return JSONResponse({
                "success": True,
                "has_container": False,
                "status": None
            })
        
        container_id, name, port, status, started_at = result
        
        # Get Docker status
        docker_status = DockerManager.get_container_status(container_id)
        
        return JSONResponse({
            "success": True,
            "has_container": True,
            "container_id": container_id,
            "container_name": name,
            "port": int(port),
            "status": status,
            "docker_status": docker_status.get('status', 'unknown'),
            "started_at": started_at,
            "access_url": f"http://127.0.0.1:{port}"
        })
        
    except Exception as e:
        print(f"Error getting container status: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

@app.get("/user/challenges/{challenge_id}/progress")
def get_challenge_progress(challenge_id: int, email: str):
    """Get user's progress on a challenge"""
    try:
        user = get_user_by_email(email)
        if not user:
            return JSONResponse({"progress": 0})
        
        user_id = user[0]
        conn = get_db()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT progress
            FROM user_challenges
            WHERE user_id = ? AND challenge_id = ?
        """, (user_id, challenge_id))
        
        result = cur.fetchone()
        conn.close()
        
        if result:
            return JSONResponse({"progress": result[0]})
        else:
            return JSONResponse({"progress": 0})
    except Exception as e:
        print(f"Error getting challenge progress: {e}")
        return JSONResponse({"progress": 0})

@app.get("/user/challenges/my-containers")
def get_user_containers(email: str):
    """Get all running containers for user"""
    try:
        # Get user
        user = get_user_by_email(email)
        if not user:
            return JSONResponse({"success": False, "error": "User not found"}, status_code=404)
        
        user_id = user[0]
        
        # Get all running containers
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT rc.container_id, rc.container_name, rc.port_mapping, rc.status, rc.started_at, c.title
            FROM running_containers rc
            JOIN challenges c ON rc.challenge_id = c.id
            WHERE rc.user_id = ? AND rc.status = 'running'
            ORDER BY rc.started_at DESC
        """, (user_id,))
        results = cur.fetchall()
        conn.close()
        
        containers = []
        for r in results:
            containers.append({
                "container_id": r[0],
                "container_name": r[1],
                "port": int(r[2]),
                "status": r[3],
                "started_at": r[4],
                "challenge_title": r[5],
                "access_url": f"http://127.0.0.1:{r[2]}"
            })
        
        return JSONResponse({
            "success": True,
            "containers": containers,
            "count": len(containers)
        })
        
    except Exception as e:
        print(f"Error getting user containers: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)
@app.get("/user/api/challenges")
def get_published_challenges(email: str = None):
    """Get all published challenges for users with lock status"""
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, title, category, difficulty, points, description, docker_image, created_at
        FROM challenges
        WHERE is_published = 1
        ORDER BY created_at ASC
    """)
    
    rows = cur.fetchall()
    
    # Get user's completed challenges if email is provided
    completed_challenge_ids = set()
    if email:
        try:
            user = get_user_by_email(email)
            if user:
                user_id = user[0]
                cur.execute("""
                    SELECT challenge_id
                    FROM user_challenges
                    WHERE user_id = ? AND progress >= 100
                """, (user_id,))
                completed_challenge_ids = {r[0] for r in cur.fetchall()}
        except:
            pass
    
    conn.close()

    challenges = []
    challenge_position = 0
    
    for r in rows:
        challenge_id = r[0]
        is_locked = False
        
        # First challenge is always unlocked
        if challenge_position > 0:
            # Check if previous challenge is completed
            # Find previous challenge ID from original rows
            if challenge_position > 0:
                previous_challenge_id = rows[challenge_position - 1][0]
                if previous_challenge_id not in completed_challenge_ids:
                    is_locked = True
        
        challenges.append({
            "id": challenge_id,
            "title": r[1],
            "category": r[2],
            "difficulty": r[3],
            "points": r[4],
            "description": r[5],
            "docker_image": r[6],
            "created_at": r[7],
            "is_locked": is_locked,
            "is_completed": challenge_id in completed_challenge_ids
        })
        
        challenge_position += 1

    return JSONResponse(
        {"success": True, "challenges": challenges},
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}
    )

@app.post("/user/challenges/create")
def create_challenge_user(
    title: str = Form(...),
    category: str = Form("Web"),
    difficulty: str = Form("Easy"),
    points: int = Form(100),
    description: str = Form(...)
):
    """Create a challenge from user form (form data)"""
    conn = get_db()
    cur = conn.cursor()

    title = title.strip()
    category = category.strip()
    difficulty = difficulty.strip()
    description = description.strip()

    if not title or not description:
        conn.close()
        return JSONResponse({"success": False, "error": "Missing required fields"}, status_code=400)

    try:
        # Create challenge with is_published=1 so it appears to users immediately
        cur.execute("""
            INSERT INTO challenges (title, category, difficulty, points, description, flag, docker_image, is_published)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1)
        """, (title, category, difficulty, points, description, "placeholder_flag", "placeholder_image"))

        conn.commit()
        challenge_id = cur.lastrowid
        conn.close()

        return JSONResponse({"success": True, "id": challenge_id, "message": "Challenge created successfully"})
    except Exception as e:
        conn.close()
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

@app.post("/user/api/challenges/{challenge_id}/verify-flag")
def verify_flag(challenge_id: int, body: dict = Body(...)):
    """Verify the flag submitted by user for a challenge"""
    try:
        email = body.get('email', '').strip()
        submitted_flag = body.get('flag', '').strip()

        if not email or not submitted_flag:
            return JSONResponse({"success": False, "error": "Missing email or flag"}, status_code=400)

        # Get user
        user = get_user_by_email(email)
        if not user:
            return JSONResponse({"success": False, "error": "User not found"}, status_code=404)

        user_id = user[0]

        # Get challenge
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id, flag, points FROM challenges WHERE id = ?", (challenge_id,))
        challenge = cur.fetchone()

        if not challenge:
            conn.close()
            return JSONResponse({"success": False, "error": "Challenge not found"}, status_code=404)

        challenge_id, correct_flag, points = challenge
        
        # Check if challenge is locked
        cur.execute("""
            SELECT id FROM challenges
            WHERE is_published = 1
            ORDER BY created_at ASC
        """)
        all_challenges = [r[0] for r in cur.fetchall()]
        
        # Find challenge position
        challenge_position = None
        for pos, cid in enumerate(all_challenges):
            if cid == challenge_id:
                challenge_position = pos
                break
        
        # Check if it's locked (first challenge is always unlocked)
        if challenge_position is not None and challenge_position > 0:
            previous_challenge_id = all_challenges[challenge_position - 1]
            cur.execute("""
                SELECT progress FROM user_challenges
                WHERE user_id = ? AND challenge_id = ? AND progress >= 100
            """, (user_id, previous_challenge_id))
            
            if not cur.fetchone():
                conn.close()
                return JSONResponse({"success": False, "error": "Challenge is locked. Complete previous challenge first."}, status_code=403)

        # Check if user already completed this challenge
        cur.execute("SELECT progress FROM user_challenges WHERE user_id = ? AND challenge_id = ?", (user_id, challenge_id))
        existing = cur.fetchone()
        already_completed = existing and existing[0] == 100
        
        print(f"[VERIFY-FLAG] User {user_id}, Challenge {challenge_id}, Already completed: {already_completed}", flush=True)

        # Check if flag is correct (case-insensitive)
        if submitted_flag.lower() == correct_flag.lower():
            print(f"[VERIFY-FLAG] Flag is CORRECT!", flush=True)
            
            container_stopped = False
            stop_message = ""
            
            # Find running container for this user and challenge to stop it automatically
            cur.execute("""
                SELECT container_id FROM running_containers 
                WHERE user_id = ? AND challenge_id = ? AND status = 'running'
            """, (user_id, challenge_id))
            container_row = cur.fetchone()
            
            if container_row:
                container_id = container_row[0]
                print(f"[VERIFY-FLAG] Found running container: {container_id}, stopping it...", flush=True)
                stop_result = DockerManager.stop_container(container_id)
                
                if stop_result.get("success"):
                    container_stopped = True
                    stop_message = "Container stopped successfully."
                    # Update status in DB
                    cur.execute("""
                        UPDATE running_containers 
                        SET status = 'stopped', stopped_at = CURRENT_TIMESTAMP
                        WHERE container_id = ?
                    """, (container_id,))
                else:
                    stop_message = f"Warning: Failed to stop container - {stop_result.get('error')}"
                    print(f"[VERIFY-FLAG] {stop_message}", flush=True)

            # Only award points on first successful completion
            if not already_completed:
                print(f"[LEVEL-UPDATE] First-time completion - updating level strictly", flush=True)
                
                # Get current challenges_solved so we can calculate new level after increment
                cur.execute("SELECT challenges_solved FROM users WHERE id = ?", (user_id,))
                current_challenges = cur.fetchone()[0]
                # After increment: new_challenges = current_challenges + 1
                # Therefore: new_level = (current_challenges + 1) + 1 = current_challenges + 2
                new_level = current_challenges + 2
                
                cur.execute("""
                    UPDATE users 
                    SET xp = xp + ?, 
                        challenges_solved = challenges_solved + 1,
                        level = ?
                    WHERE id = ?
                """, (points, new_level, user_id))
                
                print(f"[LEVEL-UPDATE] Update executed - level set to {new_level} (strict formula)", flush=True)
                points_awarded = points
            else:
                print(f"[VERIFY-FLAG] Challenge already completed, no points awarded", flush=True)
                points_awarded = 0

            # Log the attempt - mark as completed with progress = 100
            cur.execute("""
                INSERT INTO user_challenges (user_id, challenge_id, progress)
                VALUES (?, ?, 100)
                ON CONFLICT(user_id, challenge_id) DO UPDATE SET
                progress = 100
            """, (user_id, challenge_id))

            conn.commit()
            conn.close()
            
            # Update challenge_solves table
            update_challenge_solves(challenge_id)
            
            # Get updated XP after the update
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT xp FROM users WHERE id = ?", (user_id,))
            updated_xp = cur.fetchone()[0]
            conn.close()

            return JSONResponse({
                "success": True,
                "message": f"Flag is correct! {stop_message}".strip(),
                "points": points_awarded,
                "total_xp": updated_xp,
                "container_stopped": container_stopped
            })
        else:
            conn.close()
            return JSONResponse({
                "success": False,
                "error": "Incorrect flag. Try again!"
            }, status_code=400)

    except Exception as e:
        print(f"Error verifying flag: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)
@app.post("/admin/api/challenges/{challenge_id}/verify-flag")
def admin_verify_flag(challenge_id: int, body: dict = Body(...)):
    """Verify the flag submitted during preview and stop container"""
    try:
        submitted_flag = body.get('flag', '').strip()
        container_id = body.get('container_id', '').strip()

        print(f"[VERIFY-FLAG] Challenge ID: {challenge_id}, Container ID: {container_id[:12] if container_id else 'None'}, Flag received: {submitted_flag[:20]}...", flush=True)

        if not submitted_flag:
            return JSONResponse({"success": False, "error": "Missing flag"}, status_code=400)

        # Get challenge and verify flag
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id, flag, points FROM challenges WHERE id = ?", (challenge_id,))
        challenge = cur.fetchone()

        if not challenge:
            conn.close()
            return JSONResponse({"success": False, "error": "Challenge not found"}, status_code=404)

        challenge_id, correct_flag, points = challenge
        print(f"[VERIFY-FLAG] Correct flag from DB: {correct_flag}, Submitted flag: {submitted_flag}", flush=True)

        # Check if flag is correct (case-insensitive)
        if submitted_flag.lower() == correct_flag.lower():
            print(f"[VERIFY-FLAG] Flag is CORRECT!", flush=True)
            
            stop_message = ""
            container_stopped = False
            
            # Stop the container if container_id provided
            if container_id:
                print(f"[VERIFY-FLAG] Attempting to stop container: {container_id}", flush=True)
                stop_result = DockerManager.stop_container(container_id)
                print(f"[VERIFY-FLAG] Container stop result: {stop_result}", flush=True)
                
                if stop_result.get("success"):
                    stop_message = "Container stopped and removed successfully."
                    container_stopped = True
                    print(f"[VERIFY-FLAG] Container stopped successfully", flush=True)
                    
                    # Update database to mark container as stopped
                    cur.execute("""
                        UPDATE running_containers 
                        SET status = 'stopped', stopped_at = CURRENT_TIMESTAMP
                        WHERE container_id = ?
                    """, (container_id,))
                    print(f"[VERIFY-FLAG] Updated database: container marked as stopped", flush=True)
                else:
                    print(f"[VERIFY-FLAG] Warning: Failed to stop container - {stop_result.get('error')}", flush=True)
                    stop_message = f"Warning: Container stop failed - {stop_result.get('error')}"
            else:
                print("[VERIFY-FLAG] No container ID provided - container will not be stopped", flush=True)
                stop_message = "No container ID provided."
            
            conn.commit()
            conn.close()
            
            return JSONResponse({
                "success": True,
                "message": f"Flag is correct! {stop_message}",
                "points": points,
                "container_stopped": container_stopped
            })
        else:
            print(f"[VERIFY-FLAG] Flag is INCORRECT!", flush=True)
            conn.close()
            return JSONResponse({
                "success": False,
                "error": "Incorrect flag. Try again!"
            }, status_code=400)

    except Exception as e:
        print(f"[VERIFY-FLAG] Error verifying admin flag: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

@app.get("/user/challenge/{challenge_id}", response_class=HTMLResponse)
def user_challenge_attempt(challenge_id: int, email: str):
    """Display challenge attempt page with embedded simulator"""
    user = get_user_by_email(email)
    if not user:
        return RedirectResponse("/user/login")

    user_id, name, email, level, xp, challenges_solved, rank = user

    # Load the challenge attempt page
    html = open(
        os.path.join(FRONTEND_DIR, "user_challenge_attempt.html"),
        encoding="utf-8"
    ).read()

    return HTMLResponse(
        html.replace("{{EMAIL}}", email)
            .replace("{{USERNAME}}", name)
            .replace("{{LEVEL}}", str(level))
    )

@app.get("/user/challenges", response_class=HTMLResponse)
def user_challenges(email: str):
    """Display published challenges to user (with Docker preview)"""
    user = get_user_by_email(email)
    if not user:
        return RedirectResponse("/user/login")

    user_id, name, email, level, xp, challenges_solved, rank = user

    html = open(
        os.path.join(FRONTEND_DIR, "user_challenges.html"),
        encoding="utf-8"
    ).read()

    return HTMLResponse(
        html.replace("{{EMAIL}}", email)
            .replace("{{USERNAME}}", name)
            .replace("{{LEVEL}}", str(level))
    )

@app.get("/user/challenge-preview", response_class=HTMLResponse)
def user_challenge_preview(id: int, email: str):
    """Display challenge preview with Docker simulator (similar to admin preview)"""
    user = get_user_by_email(email)
    if not user:
        return RedirectResponse("/user/login")

    user_id = user[0]
    challenge_id = id
    
    # Check if challenge is locked
    conn = get_db()
    cur = conn.cursor()
    
    # Get all published challenges in order
    cur.execute("""
        SELECT id FROM challenges
        WHERE is_published = 1
        ORDER BY created_at ASC
    """)
    all_challenges = [r[0] for r in cur.fetchall()]
    
    # Find challenge position
    challenge_position = None
    for pos, cid in enumerate(all_challenges):
        if cid == challenge_id:
            challenge_position = pos
            break
    
    # Check if challenge exists and is published
    cur.execute("SELECT id FROM challenges WHERE id = ? AND is_published = 1", (challenge_id,))
    if not cur.fetchone():
        conn.close()
        return RedirectResponse(f"/user/challenges?email={email}")
    
    # Check if it's locked (first challenge is always unlocked)
    if challenge_position is not None and challenge_position > 0:
        previous_challenge_id = all_challenges[challenge_position - 1]
        cur.execute("""
            SELECT progress FROM user_challenges
            WHERE user_id = ? AND challenge_id = ? AND progress >= 100
        """, (user_id, previous_challenge_id))
        
        if not cur.fetchone():
            conn.close()
            # Challenge is locked, redirect back
            return RedirectResponse(f"/user/challenges?email={email}")
    
    # Mark challenge as started (progress = 50)
    try:
        cur.execute("""
            INSERT INTO user_challenges (user_id, challenge_id, progress)
            VALUES (?, ?, 50)
            ON CONFLICT(user_id, challenge_id) DO UPDATE SET
            progress = CASE 
                WHEN progress < 100 THEN 50
                ELSE progress
            END
        """, (user_id, challenge_id))
        conn.commit()
    except Exception as e:
        print(f"Error marking challenge as started: {e}")
    finally:
        conn.close()

    html = open(
        os.path.join(FRONTEND_DIR, "user_challenge_preview.html"),
        encoding="utf-8"
    ).read()

    return HTMLResponse(html)

# ================== ADMIN LEADERBOARD (DB CONNECTED) ==================
@app.get("/admin/leaderboard", response_class=HTMLResponse)
def admin_leaderboard():
    conn = get_db()
    cur = conn.cursor()

    # -------- BASIC STATS --------
    cur.execute("SELECT COUNT(*) FROM users")
    total_users = cur.fetchone()[0]

    active_today = 0  # keep 0 for now (no activity table yet)

    # -------- FETCH USERS FOR RANKING WITH TOTAL SCORE (XP already includes tutorial points) --------
    cur.execute("""
        SELECT u.name, u.email, u.level, u.challenges_solved, u.xp as total_score
        FROM users u
        ORDER BY u.xp DESC, u.level DESC, u.id ASC
    """)
    users = cur.fetchall()

    conn.close()

    # -------- TOP SCORE & AVG SCORE --------
    if users:
        top_score = max(u[4] for u in users)
        avg_score = int(sum(u[4] for u in users) / len(users))
    else:
        top_score = 0
        avg_score = 0

    # -------- PODIUM (TOP 3) --------
    podium = []
    for i in range(3):
        if i < len(users):
            podium.append(users[i])
        else:
            podium.append(("User", "email@example.com", 0, 0, 0))

    # -------- BUILD FULL RANKINGS (WITH TOTAL SCORE: CHALLENGE XP + TUTORIAL POINTS) --------
    full_rows = ""
    for idx, (name, email, level, solved, total_score) in enumerate(users, start=1):
        medal = ""
        if idx == 1:
            medal = "🥇"
        elif idx == 2:
            medal = "🥈"
        elif idx == 3:
            medal = "🥉"

        full_rows += f"""
        <tr>
            <td>{medal} #{idx}</td>
            <td>{name}</td>
            <td class="score">{total_score}</td>
            <td>{solved}</td>
        </tr>
        """

    html = open(
        os.path.join(FRONTEND_DIR, "admin_leaderboard.html"),
        encoding="utf-8"
    ).read()

    return HTMLResponse(
        html
        .replace("{{TOTAL_USERS}}", str(total_users))
        .replace("{{ACTIVE_TODAY}}", str(active_today))
        .replace("{{TOP_SCORE}}", str(top_score))
        .replace("{{AVG_SCORE}}", str(avg_score))

        # GOLD
        .replace("{{FIRST_NAME}}", podium[0][0])
        .replace("{{FIRST_SCORE}}", str(podium[0][4]))
        .replace("{{FIRST_SOLVED}}", str(podium[0][3]))
        .replace("{{FIRST_EMAIL}}", podium[0][1])

        # SILVER
        .replace("{{SECOND_NAME}}", podium[1][0])
        .replace("{{SECOND_SCORE}}", str(podium[1][4]))
        .replace("{{SECOND_SOLVED}}", str(podium[1][3]))
        .replace("{{SECOND_EMAIL}}", podium[1][1])

        # BRONZE
        .replace("{{THIRD_NAME}}", podium[2][0])
        .replace("{{THIRD_SCORE}}", str(podium[2][4]))
        .replace("{{THIRD_SOLVED}}", str(podium[2][3]))
        .replace("{{THIRD_EMAIL}}", podium[2][1])

        # FULL TABLE
        .replace("{{FULL_RANKING_ROWS}}", full_rows)
    )


# ================== ADMIN USER MANAGEMENT API ==================
@app.get("/admin/api/users")
def get_all_users():
    """Get all users with their stats for admin management"""
    try:
        def fetch_users():
            conn = get_db()
            cur = conn.cursor()

            # Fetch all users with calculated total score (xp already includes tutorial points)
            cur.execute("""
                SELECT u.id, u.name, u.email, u.level, u.xp, u.challenges_solved, u.global_rank,
                       u.xp as total_score, u.status, u.is_locked
                FROM users u
                ORDER BY u.xp DESC, u.level DESC
            """)
            
            rows = cur.fetchall()
            users = []
            for row in rows:
                users.append({
                    "id": row[0],
                    "name": row[1],
                    "email": row[2],
                    "level": row[3],
                    "xp": row[4],
                    "challenges_solved": row[5],
                    "global_rank": row[6],
                    "total_score": row[7],
                    "status": row[8],
                    "is_locked": bool(row[9])
                })
            
            conn.close()
            return users
        
        users = execute_with_retry(fetch_users)
        return JSONResponse({"success": True, "users": users})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.put("/admin/api/users/{user_id}")
def update_user(user_id: int, body: dict = Body(...)):
    """Update user details (name, email, level, xp)"""
    try:
        name = body.get('name', '').strip()
        email = body.get('email', '').strip()
        level = body.get('level', 1)
        xp = body.get('xp', 0)
        
        if not name or not email:
            return JSONResponse({"success": False, "error": "Name and email are required"}, status_code=400)
        
        def update_db():
            conn = get_db()
            cur = conn.cursor()
            
            # Update user
            cur.execute("""
                UPDATE users 
                SET name = ?, email = ?, level = ?, xp = ?
                WHERE id = ?
            """, (name, email, level, xp, user_id))
            
            conn.commit()
            conn.close()
        
        execute_with_retry(update_db)
        return JSONResponse({"success": True, "message": "User updated successfully"})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.delete("/admin/api/users/{user_id}")
def delete_user(user_id: int):
    """Delete a user and all their associated data"""
    try:
        def delete_db():
            conn = get_db()
            cur = conn.cursor()
            
            # Get user info first (we need the username for tutorial_progress)
            cur.execute("SELECT name FROM users WHERE id = ?", (user_id,))
            user_result = cur.fetchone()
            if not user_result:
                raise Exception(f"User with ID {user_id} not found")
            
            username = user_result[0]
            
            # Delete associated data
            cur.execute("DELETE FROM user_challenges WHERE user_id = ?", (user_id,))
            cur.execute("DELETE FROM tutorial_progress WHERE username = ?", (username,))
            cur.execute("DELETE FROM attempt_history WHERE user_id = ?", (user_id,))
            cur.execute("DELETE FROM saved_tutorials WHERE user_id = ?", (user_id,))
            cur.execute("DELETE FROM tutorial_ratings WHERE user_id = ?", (user_id,))
            cur.execute("DELETE FROM running_containers WHERE user_id = ?", (user_id,))
            
            # Delete user
            cur.execute("DELETE FROM users WHERE id = ?", (user_id,))
            
            conn.commit()
            conn.close()
        
        execute_with_retry(delete_db)
        return JSONResponse({"success": True, "message": "User deleted successfully"})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/admin/api/users/{user_id}/reset-progress")
def reset_user_progress(user_id: int):
    """Reset all progress for a user (challenges and tutorials)"""
    try:
        print(f"[DEBUG] Starting reset progress for user {user_id}")
        
        def reset_db():
            conn = get_db()
            cur = conn.cursor()
            
            try:
                # Verify user exists and get username
                cur.execute("SELECT id, name FROM users WHERE id = ?", (user_id,))
                user = cur.fetchone()
                if not user:
                    raise Exception(f"User with ID {user_id} not found")
                
                username = user[1]
                print(f"[DEBUG] User {user_id} ({username}) found, resetting progress...")
                
                # Reset user stats
                cur.execute("""
                    UPDATE users 
                    SET xp = 0, challenges_solved = 0, level = 1
                    WHERE id = ?
                """, (user_id,))
                
                # Clear user challenges
                cur.execute("DELETE FROM user_challenges WHERE user_id = ?", (user_id,))
                
                # Clear tutorial progress (uses username, not user_id)
                cur.execute("DELETE FROM tutorial_progress WHERE username = ?", (username,))
                
                # Clear attempt history
                cur.execute("DELETE FROM attempt_history WHERE user_id = ?", (user_id,))
                
                # Clear saved tutorials
                cur.execute("DELETE FROM saved_tutorials WHERE user_id = ?", (user_id,))
                
                # Clear tutorial ratings
                cur.execute("DELETE FROM tutorial_ratings WHERE user_id = ?", (user_id,))
                
                # Clear running containers for challenges
                cur.execute("DELETE FROM running_containers WHERE user_id = ?", (user_id,))
                
                conn.commit()
                print(f"[DEBUG] Successfully reset progress for user {user_id}")
            finally:
                conn.close()
        
        execute_with_retry(reset_db)
        return JSONResponse({"success": True, "message": "User progress reset successfully"})
    except Exception as e:
        import traceback
        error_msg = str(e)
        print(f"[ERROR] Failed to reset progress for user {user_id}: {error_msg}")
        traceback.print_exc()
        return JSONResponse({"success": False, "error": error_msg}, status_code=500)


# ===== ENTERPRISE FEATURES =====

# User Status Management
@app.post("/admin/api/users/{user_id}/ban")
def ban_user(user_id: int):
    """Ban a user from the platform"""
    try:
        def ban_db():
            conn = get_db()
            cur = conn.cursor()
            
            cur.execute("UPDATE users SET status = 'banned' WHERE id = ?", (user_id,))
            cur.execute("""
                INSERT INTO audit_logs (admin_email, action, target_user_id, new_value)
                VALUES ('admin', 'USER_BANNED', ?, 'user_banned')
            """, (user_id,))
            
            conn.commit()
            conn.close()
        
        execute_with_retry(ban_db)
        return JSONResponse({"success": True, "message": "User banned successfully"})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/admin/api/users/{user_id}/unban")
def unban_user(user_id: int):
    """Unban a user"""
    try:
        def unban_db():
            conn = get_db()
            cur = conn.cursor()
            
            cur.execute("UPDATE users SET status = 'active' WHERE id = ?", (user_id,))
            cur.execute("""
                INSERT INTO audit_logs (admin_email, action, target_user_id, new_value)
                VALUES ('admin', 'USER_UNBANNED', ?, 'user_unbanned')
            """, (user_id,))
            
            conn.commit()
            conn.close()
        
        execute_with_retry(unban_db)
        return JSONResponse({"success": True, "message": "User unbanned successfully"})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/admin/api/users/{user_id}/lock")
def lock_user(user_id: int):
    """Lock user account (prevent login)"""
    try:
        def lock_db():
            conn = get_db()
            cur = conn.cursor()
            
            cur.execute("UPDATE users SET is_locked = 1 WHERE id = ?", (user_id,))
            cur.execute("""
                INSERT INTO audit_logs (admin_email, action, target_user_id, new_value)
                VALUES ('admin', 'ACCOUNT_LOCKED', ?, 'account_locked')
            """, (user_id,))
            
            conn.commit()
            conn.close()
        
        execute_with_retry(lock_db)
        return JSONResponse({"success": True, "message": "User account locked"})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/admin/api/users/{user_id}/unlock")
def unlock_user(user_id: int):
    """Unlock user account"""
    try:
        def unlock_db():
            conn = get_db()
            cur = conn.cursor()
            
            cur.execute("UPDATE users SET is_locked = 0 WHERE id = ?", (user_id,))
            cur.execute("""
                INSERT INTO audit_logs (admin_email, action, target_user_id, new_value)
                VALUES ('admin', 'ACCOUNT_UNLOCKED', ?, 'account_unlocked')
            """, (user_id,))
            
            conn.commit()
            conn.close()
        
        execute_with_retry(unlock_db)
        return JSONResponse({"success": True, "message": "User account unlocked"})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# Activity and Login History
@app.get("/admin/api/users/{user_id}/login-history")
def get_user_login_history(user_id: int):
    """Get login history for a specific user"""
    try:
        def fetch_history():
            conn = get_db()
            cur = conn.cursor()
            
            cur.execute("""
                SELECT id, login_time, logout_time, ip_address
                FROM login_history
                WHERE user_id = ?
                ORDER BY login_time DESC
                LIMIT 50
            """, (user_id,))
            
            rows = cur.fetchall()
            history = []
            for row in rows:
                history.append({
                    "id": row[0],
                    "login_time": row[1],
                    "logout_time": row[2],
                    "ip_address": row[3] or "Unknown"
                })
            
            conn.close()
            return history
        
        history = execute_with_retry(fetch_history)
        return JSONResponse({"success": True, "history": history})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# Audit Logs
@app.get("/admin/api/audit-logs")
def get_audit_logs(limit: int = 100, action: str = None):
    """Get admin audit logs"""
    try:
        def fetch_logs():
            conn = get_db()
            cur = conn.cursor()
            
            if action:
                cur.execute("""
                    SELECT id, admin_email, action, target_user_email, timestamp, details
                    FROM audit_logs
                    WHERE action = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, (action, limit))
            else:
                cur.execute("""
                    SELECT id, admin_email, action, target_user_email, timestamp, details
                    FROM audit_logs
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, (limit,))
            
            rows = cur.fetchall()
            logs = []
            for row in rows:
                logs.append({
                    "id": row[0],
                    "admin_email": row[1],
                    "action": row[2],
                    "target_user": row[3],
                    "timestamp": row[4],
                    "details": row[5]
                })
            
            conn.close()
            return logs
        
        logs = execute_with_retry(fetch_logs)
        return JSONResponse({"success": True, "logs": logs})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# Bulk Operations
@app.post("/admin/api/bulk/ban-users")
def bulk_ban_users(body: dict = Body(...)):
    """Ban multiple users at once"""
    try:
        user_ids = body.get('user_ids', [])
        
        def bulk_ban():
            conn = get_db()
            cur = conn.cursor()
            
            for user_id in user_ids:
                cur.execute("UPDATE users SET status = 'banned' WHERE id = ?", (user_id,))
                cur.execute("""
                    INSERT INTO audit_logs (admin_email, action, target_user_id, new_value)
                    VALUES ('admin', 'BULK_BAN', ?, 'bulk_banned')
                """, (user_id,))
            
            conn.commit()
            conn.close()
        
        execute_with_retry(bulk_ban)
        return JSONResponse({"success": True, "message": f"Banned {len(user_ids)} users"})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/admin/api/bulk/reset-progress")
def bulk_reset_progress(body: dict = Body(...)):
    """Reset progress for multiple users"""
    try:
        user_ids = body.get('user_ids', [])
        
        def bulk_reset():
            conn = get_db()
            cur = conn.cursor()
            
            for user_id in user_ids:
                cur.execute("""
                    UPDATE users 
                    SET xp = 0, challenges_solved = 0, level = 1
                    WHERE id = ?
                """, (user_id,))
                cur.execute("DELETE FROM user_challenges WHERE user_id = ?", (user_id,))
                cur.execute("DELETE FROM tutorial_progress WHERE user_id = ?", (user_id,))
            
            conn.commit()
            conn.close()
        
        execute_with_retry(bulk_reset)
        return JSONResponse({"success": True, "message": f"Reset progress for {len(user_ids)} users"})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/admin/api/bulk/unban-users")
def bulk_unban_users(body: dict = Body(...)):
    """Unban multiple users at once"""
    try:
        user_ids = body.get('user_ids', [])
        def bulk_unban():
            conn = get_db()
            cur = conn.cursor()
            for user_id in user_ids:
                cur.execute("UPDATE users SET status = 'active' WHERE id = ? AND status = 'banned'", (user_id,))
                cur.execute("""
                    INSERT INTO audit_logs (admin_email, action, target_user_id, new_value)
                    VALUES ('admin', 'BULK_UNBAN', ?, 'bulk_unbanned')
                """, (user_id,))
            conn.commit()
            conn.close()
        execute_with_retry(bulk_unban)
        return JSONResponse({"success": True, "message": f"Unbanned {len(user_ids)} users"})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/admin/api/bulk/lock-users")
def bulk_lock_users(body: dict = Body(...)):
    """Lock multiple users at once"""
    try:
        user_ids = body.get('user_ids', [])
        def bulk_lock():
            conn = get_db()
            cur = conn.cursor()
            for user_id in user_ids:
                cur.execute("UPDATE users SET is_locked = 1 WHERE id = ?", (user_id,))
                cur.execute("""
                    INSERT INTO audit_logs (admin_email, action, target_user_id, new_value)
                    VALUES ('admin', 'BULK_LOCK', ?, 'bulk_locked')
                """, (user_id,))
            conn.commit()
            conn.close()
        execute_with_retry(bulk_lock)
        return JSONResponse({"success": True, "message": f"Locked {len(user_ids)} accounts"})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/admin/api/bulk/unlock-users")
def bulk_unlock_users(body: dict = Body(...)):
    """Unlock multiple users at once"""
    try:
        user_ids = body.get('user_ids', [])
        def bulk_unlock():
            conn = get_db()
            cur = conn.cursor()
            for user_id in user_ids:
                cur.execute("UPDATE users SET is_locked = 0 WHERE id = ?", (user_id,))
                cur.execute("""
                    INSERT INTO audit_logs (admin_email, action, target_user_id, new_value)
                    VALUES ('admin', 'BULK_UNLOCK', ?, 'bulk_unlocked')
                """, (user_id,))
            conn.commit()
            conn.close()
        execute_with_retry(bulk_unlock)
        return JSONResponse({"success": True, "message": f"Unlocked {len(user_ids)} accounts"})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# Export/Reporting
@app.get("/admin/api/export/users")
def export_users_csv():
    """Export all users as CSV"""
    try:
        def fetch_users():
            conn = get_db()
            cur = conn.cursor()
            
            cur.execute("""
                SELECT id, name, email, level, xp, challenges_solved, 
                       status, is_locked
                FROM users
                ORDER BY id
            """)
            
            rows = cur.fetchall()
            conn.close()
            return rows
        
        rows = execute_with_retry(fetch_users)
        
        # Create CSV content
        csv_content = "ID,Name,Email,Level,XP,Challenges Solved,Status,Locked\n"
        for row in rows:
            # Properly escape CSV fields that might contain commas or quotes
            name = str(row[1]).replace('"', '""')  # Escape quotes
            email = str(row[2]).replace('"', '""')
            csv_content += f'{row[0]},"{name}","{email}",{row[3]},{row[4]},{row[5]},"{row[6]}",{row[7]}\n'
        
        return {
            "success": True,
            "csv": csv_content,
            "filename": f"users_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        }
    except Exception as e:
        print(f"[ERROR] CSV Export failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# Analytics Dashboard
@app.get("/admin/api/analytics/summary")
def get_analytics_summary():
    """Get platform analytics summary"""
    try:
        def fetch_analytics():
            conn = get_db()
            cur = conn.cursor()
            
            # Total users
            cur.execute("SELECT COUNT(*) FROM users")
            total_users = cur.fetchone()[0]
            
            # Active users (non-banned, non-locked)
            cur.execute("SELECT COUNT(*) FROM users WHERE status = 'active' AND is_locked = 0")
            active_users = cur.fetchone()[0]
            
            # Banned users
            cur.execute("SELECT COUNT(*) FROM users WHERE status = 'banned'")
            banned_users = cur.fetchone()[0]
            
            # Locked users
            cur.execute("SELECT COUNT(*) FROM users WHERE is_locked = 1")
            locked_users = cur.fetchone()[0]
            
            # Average XP
            cur.execute("SELECT AVG(xp) FROM users WHERE status = 'active'")
            avg_xp = int(cur.fetchone()[0] or 0)
            
            # Total published challenges
            cur.execute("SELECT COUNT(*) FROM challenges WHERE is_published = 1")
            total_challenges = cur.fetchone()[0]

            # Total published tutorials
            cur.execute("SELECT COUNT(*) FROM tutorials WHERE is_published = 1")
            total_tutorials = cur.fetchone()[0]
            
            # Historical growth (last 7 days)
            growth_data = []
            growth_labels = []
            for i in range(6, -1, -1):
                cur.execute(f"SELECT COUNT(*) FROM users WHERE DATE(created_at) = DATE('now', '-{i} days')")
                count = cur.fetchone()[0]
                cur.execute(f"SELECT DATE('now', '-{i} days')")
                date_str = cur.fetchone()[0]
                growth_data.append(count)
                growth_labels.append(date_str)

            # Status distribution (categorize by lock status and user status)
            cur.execute("""
                SELECT 
                    CASE 
                        WHEN is_locked = 1 THEN 'Locked'
                        WHEN status = 'banned' THEN 'Banned'
                        WHEN status = 'inactive' THEN 'Inactive'
                        WHEN status = 'active' THEN 'Active'
                        ELSE status
                    END as category,
                    COUNT(*) as count
                FROM users
                GROUP BY 
                    CASE 
                        WHEN is_locked = 1 THEN 'Locked'
                        WHEN status = 'banned' THEN 'Banned'
                        WHEN status = 'inactive' THEN 'Inactive'
                        WHEN status = 'active' THEN 'Active'
                        ELSE status
                    END
            """)
            status_dist = dict(cur.fetchall())

            # Users joined today
            today_signups = growth_data[-1] if growth_data else 0
            
            # Logins today
            cur.execute("""
                SELECT COUNT(*) FROM login_history 
                WHERE DATE(login_time) = DATE('now')
            """)
            today_logins = cur.fetchone()[0]

            conn.close()
            
            return {
                "total_users": total_users,
                "active_users": active_users,
                "banned_users": banned_users,
                "locked_users": locked_users,
                "avg_xp": avg_xp,
                "total_challenges": total_challenges,
                "total_tutorials": total_tutorials,
                "today_signups": today_signups,
                "today_logins": today_logins,
                "growth_labels": growth_labels,
                "growth_data": growth_data,
                "status_dist": status_dist
            }
        
        analytics = execute_with_retry(fetch_analytics)
        return JSONResponse({"success": True, "analytics": analytics})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/admin/api/analytics/retention")
def get_user_retention():
    """Get user retention metrics"""
    try:
        def fetch_retention():
            conn = get_db()
            cur = conn.cursor()
            
            # Total users created in last 30 days
            cur.execute("""
                SELECT COUNT(*) FROM users 
                WHERE DATE(created_at) >= DATE('now', '-30 days')
            """)
            new_users_30d = cur.fetchone()[0]
            
            # Retained users (logged in last 7 days)
            cur.execute("""
                SELECT COUNT(*) FROM users 
                WHERE DATE(created_at) >= DATE('now', '-30 days')
                AND DATE(last_login) >= DATE('now', '-7 days')
            """)
            retained_users = cur.fetchone()[0]
            
            # Churn rate
            retention_rate = (retained_users / new_users_30d * 100) if new_users_30d > 0 else 0
            
            # Weekly active users
            cur.execute("""
                SELECT COUNT(DISTINCT user_id) FROM login_history
                WHERE DATE(login_time) >= DATE('now', '-7 days')
            """)
            weekly_active = cur.fetchone()[0]
            
            # Monthly active users
            cur.execute("""
                SELECT COUNT(DISTINCT user_id) FROM login_history
                WHERE DATE(login_time) >= DATE('now', '-30 days')
            """)
            monthly_active = cur.fetchone()[0]
            
            conn.close()
            
            return {
                "new_users_30d": new_users_30d,
                "retained_users": retained_users,
                "retention_rate": round(retention_rate, 1),
                "weekly_active": weekly_active,
                "monthly_active": monthly_active
            }
        
        retention = execute_with_retry(fetch_retention)
        return JSONResponse({"success": True, "retention": retention})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/admin/api/analytics/system-health")
def get_system_health():
    """Get system health metrics"""
    try:
        def fetch_health():
            conn = get_db()
            cur = conn.cursor()
            
            # Total XP distributed
            cur.execute("SELECT SUM(xp) FROM users WHERE status = 'active'")
            total_xp = cur.fetchone()[0] or 0
            
            # Average user level
            cur.execute("SELECT AVG(level) FROM users WHERE status = 'active'")
            avg_level = round(cur.fetchone()[0] or 0, 1)
            
            # Total tutorials
            cur.execute("SELECT COUNT(*) FROM tutorials")
            total_tutorials = cur.fetchone()[0]
            
            # Platform uptime (assuming always up - 100%)
            uptime = 100.0
            
            # Database size (estimated from record counts)
            cur.execute("SELECT COUNT(*) FROM users")
            user_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM challenges")
            challenge_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM tutorials")
            tutorial_count = cur.fetchone()[0]
            
            conn.close()
            
            return {
                "total_xp": total_xp,
                "avg_level": avg_level,
                "total_tutorials": total_tutorials,
                "uptime": uptime,
                "database_records": user_count + challenge_count + tutorial_count
            }
        
        health = execute_with_retry(fetch_health)
        return JSONResponse({"success": True, "health": health})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/admin/api/analytics/activity-timeline")
def get_activity_timeline(days: int = 7):
    """Get user activity timeline"""
    try:
        def fetch_timeline():
            conn = get_db()
            cur = conn.cursor()
            
            timeline = []
            for i in range(days - 1, -1, -1):
                # Logins from login_history
                cur.execute(f"""
                    SELECT COUNT(DISTINCT user_id) as logins
                    FROM login_history
                    WHERE DATE(login_time) = DATE('now', '-{i} days')
                """)
                logins_row = cur.fetchone()
                logins = logins_row[0] if logins_row and logins_row[0] else 0
                
                # Challenges completed
                cur.execute(f"""
                    SELECT COUNT(*) as challenges
                    FROM user_challenges
                    WHERE DATE(solved_at) = DATE('now', '-{i} days')
                    AND solved_at IS NOT NULL
                """)
                challenges_row = cur.fetchone()
                challenges = challenges_row[0] if challenges_row and challenges_row[0] else 0
                
                cur.execute(f"SELECT DATE('now', '-{i} days')")
                date = cur.fetchone()[0]
                
                timeline.append({
                    "date": date,
                    "logins": logins,
                    "challenges": challenges
                })
            
            conn.close()
            return timeline
        
        timeline = execute_with_retry(fetch_timeline)
        return JSONResponse({"success": True, "timeline": timeline})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/admin/api/analytics/difficulty-analysis")
def get_challenge_difficulty():
    """Get challenge difficulty analysis"""
    try:
        def fetch_difficulty():
            conn = get_db()
            cur = conn.cursor()
            
            cur.execute("""
                SELECT 
                    c.id,
                    c.title,
                    c.difficulty,
                    COUNT(DISTINCT uc.user_id) as attempts,
                    AVG(uc.progress) as avg_progress,
                    MIN(uc.progress) as min_progress,
                    MAX(uc.progress) as max_progress
                FROM challenges c
                LEFT JOIN user_challenges uc ON c.id = uc.challenge_id
                GROUP BY c.id, c.title, c.difficulty
                ORDER BY c.difficulty DESC, attempts DESC
            """)
            
            challenges = cur.fetchall()
            result = []
            for challenge in challenges:
                result.append({
                    "id": challenge[0],
                    "name": challenge[1],
                    "difficulty": challenge[2] or "Unknown",
                    "attempts": challenge[3] or 0,
                    "avg_time": round(challenge[4] or 0, 2),
                    "min_time": round(challenge[5] or 0, 2),
                    "max_time": round(challenge[6] or 0, 2)
                })
            
            conn.close()
            return result
        
        difficulties = execute_with_retry(fetch_difficulty)
        return JSONResponse({"success": True, "difficulties": difficulties})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/admin/api/analytics/daily-stats")
def get_daily_stats():
    """Get detailed daily statistics for the past 30 days"""
    try:
        def fetch_daily_stats():
            conn = get_db()
            cur = conn.cursor()
            
            daily_stats = []
            for i in range(29, -1, -1):
                cur.execute(f"""
                    SELECT 
                        DATE('now', '-{i} days') as date,
                        COUNT(DISTINCT CASE WHEN DATE(created_at) = DATE('now', '-{i} days') THEN id END) as new_signups,
                        COUNT(DISTINCT CASE WHEN DATE(last_login) = DATE('now', '-{i} days') THEN id END) as active_users
                    FROM users
                """)
                row = cur.fetchone()
                if row:
                    daily_stats.append({
                        "date": row[0],
                        "signups": row[1] or 0,
                        "active": row[2] or 0
                    })
            
            conn.close()
            return daily_stats
        
        stats = execute_with_retry(fetch_daily_stats)
        return JSONResponse({"success": True, "stats": stats})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/admin/api/analytics/engagement-metrics")
def get_engagement_metrics():
    """Get user engagement metrics"""
    try:
        def fetch_engagement():
            conn = get_db()
            cur = conn.cursor()
            
            # Users who completed at least one challenge
            cur.execute("""
                SELECT COUNT(DISTINCT user_id) FROM user_challenges 
                WHERE solved_at IS NOT NULL
            """)
            users_completed_challenges = cur.fetchone()[0] or 0
            
            # Average challenges per user
            cur.execute("""
                SELECT AVG(challenge_count) FROM (
                    SELECT user_id, COUNT(*) as challenge_count
                    FROM user_challenges
                    WHERE solved_at IS NOT NULL
                    GROUP BY user_id
                )
            """)
            avg_challenges_per_user = round(cur.fetchone()[0] or 0, 1)
            
            # Average session duration (estimated from login history if available)
            cur.execute("""
                SELECT AVG(1) FROM login_history
                WHERE id > 0
            """)
            avg_session = round(cur.fetchone()[0] or 0, 1)
            
            conn.close()
            
            return {
                "users_completed": users_completed_challenges,
                "avg_per_user": avg_challenges_per_user,
                "avg_session_min": avg_session
            }
        
        engagement = execute_with_retry(fetch_engagement)
        return JSONResponse({"success": True, "engagement": engagement})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/admin/api/analytics/user-distribution")
def get_user_distribution():
    """Get user distribution by level and status"""
    try:
        def fetch_distribution():
            conn = get_db()
            cur = conn.cursor()
            
            # By level
            cur.execute("""
                SELECT level, COUNT(*) as count
                FROM users
                GROUP BY level
                ORDER BY level
            """)
            by_level = {row[0]: row[1] for row in cur.fetchall()}
            
            # By XP ranges
            cur.execute("""
                SELECT 
                    CASE 
                        WHEN xp < 100 THEN '0-100'
                        WHEN xp < 500 THEN '100-500'
                        WHEN xp < 1000 THEN '500-1K'
                        WHEN xp < 5000 THEN '1K-5K'
                        ELSE '5K+'
                    END as xp_range,
                    COUNT(*) as count
                FROM users
                WHERE status = 'active'
                GROUP BY xp_range
            """)
            by_xp = {row[0]: row[1] for row in cur.fetchall()}
            
            conn.close()
            
            return {
                "by_level": by_level,
                "by_xp": by_xp
            }
        
        distribution = execute_with_retry(fetch_distribution)
        return JSONResponse({"success": True, "distribution": distribution})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/admin/api/analytics/content-stats")
def get_content_stats():
    """Get content and challenge statistics"""
    try:
        def fetch_content():
            conn = get_db()
            cur = conn.cursor()
            
            # Total content
            cur.execute("SELECT COUNT(*) FROM tutorials")
            total_tutorials = cur.fetchone()[0]
            
            cur.execute("SELECT COUNT(*) FROM challenges")
            total_challenges = cur.fetchone()[0]
            
            # Most popular tutorial
            cur.execute("""
                SELECT t.id, t.title, COUNT(DISTINCT st.user_id) as views
                FROM tutorials t
                LEFT JOIN saved_tutorials st ON t.id = st.tutorial_id
                GROUP BY t.id, t.title
                ORDER BY views DESC
                LIMIT 1
            """)
            top_tutorial = cur.fetchone()
            
            # Most completed challenge
            cur.execute("""
                SELECT c.id, c.title, COUNT(*) as completions
                FROM challenges c
                LEFT JOIN user_challenges uc ON c.id = uc.challenge_id
                WHERE uc.solved_at IS NOT NULL
                GROUP BY c.id, c.title
                ORDER BY completions DESC
                LIMIT 1
            """)
            top_challenge = cur.fetchone()
            
            conn.close()
            
            return {
                "total_tutorials": total_tutorials,
                "total_challenges": total_challenges,
                "top_tutorial": {
                    "name": top_tutorial[1] if top_tutorial else "N/A",
                    "views": top_tutorial[2] if top_tutorial else 0
                } if top_tutorial else None,
                "top_challenge": {
                    "name": top_challenge[1] if top_challenge else "N/A",
                    "completions": top_challenge[2] if top_challenge else 0
                } if top_challenge else None
            }
        
        content = execute_with_retry(fetch_content)
        return JSONResponse({"success": True, "content": content})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/admin/api/analytics/top-users")
def get_top_users(limit: int = 10):
    """Get top active users by points (challenges + tutorials)"""
    try:
        def fetch_top_users():
            conn = get_db()
            cur = conn.cursor()
            
            # Get users with their total points (XP from challenges + points from completed tutorials)
            cur.execute("""
                SELECT 
                    u.id, 
                    u.name, 
                    u.email, 
                    u.xp,
                    u.challenges_solved, 
                    u.last_login,
                    COALESCE(SUM(t.points), 0) as tutorial_score
                FROM users u
                LEFT JOIN tutorial_progress tp ON u.name = tp.username AND tp.completed = 1
                LEFT JOIN tutorials t ON tp.tutorial_id = t.id
                WHERE u.status = 'active'
                GROUP BY u.id, u.name, u.email, u.xp, u.challenges_solved, u.last_login
                ORDER BY (u.xp + COALESCE(SUM(t.points), 0)) DESC
                LIMIT ?
            """, (limit,))
            
            users = cur.fetchall()
            result = []
            for user in users:
                total_points = user[3] + user[6]  # xp + tutorial_score
                result.append({
                    "id": user[0],
                    "name": user[1],
                    "email": user[2],
                    "points": total_points,
                    "challenges": user[4],
                    "last_login": user[5]
                })
            
            conn.close()
            return result
        
        users = execute_with_retry(fetch_top_users)
        return JSONResponse({"success": True, "users": users})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/admin/api/analytics/challenge-performance")
def get_challenge_performance():
    """Get challenge performance metrics"""
    try:
        def fetch_challenge_metrics():
            conn = get_db()
            cur = conn.cursor()
            
            cur.execute("""
                SELECT 
                    c.id,
                    c.title,
                    COUNT(DISTINCT uc.user_id) as total_attempts,
                    SUM(CASE WHEN uc.solved_at IS NOT NULL THEN 1 ELSE 0 END) as completions,
                    ROUND(100.0 * SUM(CASE WHEN uc.solved_at IS NOT NULL THEN 1 ELSE 0 END) / NULLIF(COUNT(DISTINCT uc.user_id), 0), 1) as completion_rate
                FROM challenges c
                LEFT JOIN user_challenges uc ON c.id = uc.challenge_id
                GROUP BY c.id, c.title
                ORDER BY total_attempts DESC
            """)
            
            challenges = cur.fetchall()
            result = []
            for challenge in challenges:
                result.append({
                    "id": challenge[0],
                    "name": challenge[1],
                    "attempts": challenge[2] or 0,
                    "completions": challenge[3] or 0,
                    "completion_rate": challenge[4] or 0
                })
            
            conn.close()
            return result
        
        challenges = execute_with_retry(fetch_challenge_metrics)
        return JSONResponse({"success": True, "challenges": challenges})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/admin/api/analytics/trends")
def get_analytics_trends(days: int = 7):
    """Get trend comparison data"""
    try:
        def fetch_trends():
            conn = get_db()
            cur = conn.cursor()
            
            # Current period
            cur.execute(f"""
                SELECT COUNT(*) FROM users 
                WHERE DATE(created_at) >= DATE('now', '-{days} days')
            """)
            current_signups = cur.fetchone()[0]
            
            # Previous period
            cur.execute(f"""
                SELECT COUNT(*) FROM users 
                WHERE DATE(created_at) >= DATE('now', '-{days*2} days')
                AND DATE(created_at) < DATE('now', '-{days} days')
            """)
            prev_signups = cur.fetchone()[0]
            
            # Active users trend
            cur.execute("""
                SELECT COUNT(*) FROM users 
                WHERE status = 'active' AND is_locked = 0
                AND DATE(last_login) >= DATE('now', '-1 days')
            """)
            current_daily_active = cur.fetchone()[0]
            
            cur.execute("""
                SELECT COUNT(*) FROM users 
                WHERE status = 'active' AND is_locked = 0
                AND DATE(last_login) >= DATE('now', '-2 days')
                AND DATE(last_login) < DATE('now', '-1 days')
            """)
            prev_daily_active = cur.fetchone()[0]
            
            # Challenges solved trend
            cur.execute(f"""
                SELECT COUNT(*) FROM user_challenges 
                WHERE DATE(solved_at) >= DATE('now', '-{days} days')
                AND solved_at IS NOT NULL
            """)
            current_challenges = cur.fetchone()[0]
            
            cur.execute(f"""
                SELECT COUNT(*) FROM user_challenges 
                WHERE DATE(solved_at) >= DATE('now', '-{days*2} days')
                AND DATE(solved_at) < DATE('now', '-{days} days')
                AND solved_at IS NOT NULL
            """)
            prev_challenges = cur.fetchone()[0]
            
            conn.close()
            
            def calc_trend(current, prev):
                if prev == 0:
                    return 0 if current == 0 else 100
                return round(((current - prev) / prev) * 100, 1)
            
            return {
                "signups": {
                    "current": current_signups,
                    "previous": prev_signups,
                    "trend": calc_trend(current_signups, prev_signups)
                },
                "daily_active": {
                    "current": current_daily_active,
                    "previous": prev_daily_active,
                    "trend": calc_trend(current_daily_active, prev_daily_active)
                },
                "challenges": {
                    "current": current_challenges,
                    "previous": prev_challenges,
                    "trend": calc_trend(current_challenges, prev_challenges)
                }
            }
        
        trends = execute_with_retry(fetch_trends)
        return JSONResponse({"success": True, "trends": trends})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# Security Settings
@app.post("/admin/api/users/{user_id}/force-password-reset")
def force_password_reset(user_id: int):
    """Force a user to reset their password on next login"""
    try:
        def update_security():
            conn = get_db()
            cur = conn.cursor()
            
            # Update or insert security settings
            cur.execute("""
                INSERT INTO user_security_settings 
                (user_id, user_email, force_password_reset)
                SELECT id, email, 1 FROM users WHERE id = ?
                ON CONFLICT(user_id) DO UPDATE SET force_password_reset = 1
            """, (user_id,))
            
            cur.execute("""
                INSERT INTO audit_logs (admin_email, action, target_user_id, new_value)
                VALUES ('admin', 'FORCE_PASSWORD_RESET', ?, 'password_reset_required')
            """, (user_id,))
            
            conn.commit()
            conn.close()
        
        execute_with_retry(update_security)
        return JSONResponse({"success": True, "message": "Password reset required on next login"})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/admin/api/users/{user_id}/reset-failed-attempts")
def reset_failed_login_attempts(user_id: int):
    """Reset failed login attempts for a user"""
    try:
        def reset_attempts():
            conn = get_db()
            cur = conn.cursor()
            
            cur.execute("""
                UPDATE user_security_settings
                SET failed_login_attempts = 0, last_failed_attempt = NULL
                WHERE user_id = ?
            """, (user_id,))
            
            conn.commit()
            conn.close()
        
        execute_with_retry(reset_attempts)
        return JSONResponse({"success": True, "message": "Failed login attempts reset"})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# Admin Communication
@app.post("/admin/api/send-user-message")
def send_message_to_user(body: dict = Body(...)):
    """Send a message from admin to user"""
    try:
        recipient_email = body.get('recipient_email', '').strip()
        message_text = body.get('message', '').strip()
        
        if not recipient_email or not message_text:
            return JSONResponse({"success": False, "error": "Email and message required"}, status_code=400)
        
        def send_msg():
            conn = get_db()
            cur = conn.cursor()
            
            cur.execute("""
                INSERT INTO admin_messages (sender_email, recipient_email, message_text, message_type)
                VALUES ('admin@system', ?, ?, 'admin')
            """, (recipient_email, message_text))
            
            conn.commit()
            conn.close()
        
        execute_with_retry(send_msg)
        return JSONResponse({"success": True, "message": "Message sent successfully"})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/admin/api/users/{user_id}/messages")
def get_user_messages(user_id: int):
    """Get all messages sent to a user"""
    try:
        def fetch_messages():
            conn = get_db()
            cur = conn.cursor()
            
            # Get user email first
            cur.execute("SELECT email FROM users WHERE id = ?", (user_id,))
            user_email_row = cur.fetchone()
            if not user_email_row:
                return []
            
            user_email = user_email_row[0]
            
            cur.execute("""
                SELECT id, sender_email, message_text, message_type, created_at, is_read
                FROM admin_messages
                WHERE recipient_email = ?
                ORDER BY created_at DESC
            """, (user_email,))
            
            rows = cur.fetchall()
            messages = []
            for row in rows:
                messages.append({
                    "id": row[0],
                    "from": row[1],
                    "message": row[2],
                    "type": row[3],
                    "timestamp": row[4],
                    "read": row[5]
                })
            
            conn.close()
            return messages
        
        messages = execute_with_retry(fetch_messages)
        return JSONResponse({"success": True, "messages": messages})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/admin/sandbox", response_class=HTMLResponse)
def admin_sandbox():
    return open(os.path.join(FRONTEND_DIR, "admin_sandbox.html"), encoding="utf-8").read()


@app.get("/admin/api/running-containers")
def get_running_containers():
    """Get all running containers from the database"""
    try:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT 
                id,
                challenge_id,
                user_id,
                container_id,
                container_name,
                port_mapping,
                status,
                started_at,
                stopped_at
            FROM running_containers
            ORDER BY started_at DESC
        """)

        containers = cur.fetchall()
        conn.close()

        # Convert to list of dictionaries and add live stats
        container_list = []
        for container in containers:
            c_data = {
                "id": container[0],
                "challenge_id": container[1],
                "user_id": container[2],
                "container_id": container[3],
                "container_name": container[4],
                "port_mapping": container[5],
                "status": container[6],
                "started_at": container[7],
                "stopped_at": container[8]
            }
            
            # If running, get live stats from DockerManager
            if c_data["status"] == 'running' and c_data["container_id"]:
                live_stats = DockerManager.get_container_stats(c_data["container_id"])
                c_data.update(live_stats)
            else:
                # Add default empty stats for consistency
                c_data.update({
                    "cpu_percent": "0.00%",
                    "memory_usage": "N/A",
                    "memory_percent": "0.00%",
                    "net_io": "N/A",
                    "block_io": "N/A",
                    "pids": "0",
                    "image": "N/A"
                })
            
            container_list.append(c_data)

        return JSONResponse({
            "success": True,
            "containers": container_list,
            "total": len(container_list)
        })

    except Exception as e:
        print(f"Error fetching running containers: {e}")
        return JSONResponse({
            "success": False,
            "error": str(e),
            "containers": [],
            "total": 0
        }, status_code=500)


@app.post("/admin/api/stop-container")
def stop_container_admin(body: dict = Body(...)):
    """Stop a specific container and update database"""
    try:
        container_id = body.get('container_id', '').strip()

        if not container_id:
            return JSONResponse({"success": False, "error": "Missing container_id"}, status_code=400)

        # Stop the container using Docker manager
        stop_result = DockerManager.stop_container(container_id)

        if stop_result.get("success"):
            # Update database to mark container as stopped
            conn = get_db()
            cur = conn.cursor()
            
            cur.execute("""
                UPDATE running_containers 
                SET status = 'stopped', stopped_at = CURRENT_TIMESTAMP
                WHERE container_id = ?
            """, (container_id,))
            
            conn.commit()
            conn.close()

            return JSONResponse({
                "success": True,
                "message": "Container stopped and removed successfully"
            })
        else:
            return JSONResponse({
                "success": False,
                "error": stop_result.get('error', 'Failed to stop container')
            }, status_code=500)

    except Exception as e:
        print(f"Error stopping container: {e}")
        return JSONResponse({
            "success": False,
            "error": str(e)
        }, status_code=500)


@app.post("/admin/api/remove-container")
def remove_container_admin(body: dict = Body(...)):
    """Remove a stopped container from database"""
    try:
        container_id = body.get('container_id', '').strip()

        if not container_id:
            return JSONResponse({"success": False, "error": "Missing container_id"}, status_code=400)

        # Delete from database
        conn = get_db()
        cur = conn.cursor()
        
        cur.execute("""
            DELETE FROM running_containers 
            WHERE container_id = ? AND status = 'stopped'
        """, (container_id,))
        
        conn.commit()
        deleted_count = cur.rowcount
        conn.close()

        if deleted_count > 0:
            return JSONResponse({
                "success": True,
                "message": "Container removed successfully"
            })
        else:
            return JSONResponse({
                "success": False,
                "error": "Container not found or still running"
            }, status_code=400)

    except Exception as e:
        print(f"Error removing container: {e}")
        return JSONResponse({
            "success": False,
            "error": str(e)
        }, status_code=500)


@app.get("/admin/notifications", response_class=HTMLResponse)
def admin_notifications():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, title, message, target, created_at
        FROM notifications
        ORDER BY id DESC
    """)
    notifications = cur.fetchall()
    conn.close()

    rows = ""
    for nid, title, msg, target, created_at in notifications:
        rows += f"""
        <div class="notification-card glow">
            <h4>{title}</h4>
            <p>{msg}</p>
            <small>Target: {target} | {created_at}</small>

            <div class="notif-actions">
                <form action="/admin/notifications/edit/{nid}" method="get" style="display:inline;">
  <button type="submit" class="notif-btn edit-btn">
    ✏️ Edit
  </button>
</form>

<form action="/admin/notifications/delete/{nid}"
      method="get"
      style="display:inline;"
      onsubmit="return confirm('Are you sure you want to delete this notification?');">
  <button type="submit" class="notif-btn delete-btn">
    🗑 Delete
  </button>
</form>

            </div>
        </div>
        """

    html = open(
        os.path.join(FRONTEND_DIR, "admin_notifications.html"),
        encoding="utf-8"
    ).read()

    return HTMLResponse(
        html.replace("{{NOTIFICATION_LIST}}", rows)
    )

#create notification page
@app.get("/admin/notifications/create", response_class=HTMLResponse)
def create_notification_page():
    return HTMLResponse(
        open(
            os.path.join(FRONTEND_DIR, "admin_create_notification.html"),
            encoding="utf-8"
        ).read()
    )

#post notification
from datetime import datetime

@app.post("/admin/notifications/create")
def create_notification(
    title: str = Form(...),
    message: str = Form(...),
    target: str = Form("all"),
    status: str = Form("available")  # ✅ REQUIRED FIX
):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO notifications (title, message, target, created_at)
        VALUES (?, ?, ?, ?)
    """, (
        title,
        message,
        target,
        datetime.now().strftime("%Y-%m-%d %H:%M")
    ))

    conn.commit()
    conn.close()

    return RedirectResponse("/admin/notifications", status_code=303)


#delete notification
@app.get("/admin/notifications/delete/{nid}")
def delete_notification(nid: int):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("DELETE FROM notifications WHERE id=?", (nid,))
    conn.commit()
    conn.close()

    return RedirectResponse("/admin/notifications", status_code=303)

#notification edit or update option ( to load the edit page)
@app.get("/admin/notifications/edit/{nid}", response_class=HTMLResponse)
def edit_notification_page(nid: int):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, title, message, target
        FROM notifications
        WHERE id=?
    """, (nid,))
    notif = cur.fetchone()
    conn.close()

    if not notif:
        return RedirectResponse("/admin/notifications", status_code=303)

    nid, title, message, target = notif

    html = open(
        os.path.join(FRONTEND_DIR, "admin_edit_notification.html"),
        encoding="utf-8"
    ).read()

    return HTMLResponse(
        html.replace("{{ID}}", str(nid))
            .replace("{{TITLE}}", title)
            .replace("{{MESSAGE}}", message)
            .replace("{{TARGET}}", target)
    )
@app.post("/admin/notifications/edit/{nid}")
def update_notification(
    nid: int,
    title: str = Form(...),
    message: str = Form(...),
    target: str = Form(...),
    status: str = Form(...)
):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE notifications
        SET title=?, message=?, target=?
        WHERE id=?
    """, (title, message, target, nid))

    conn.commit()
    conn.close()

    return RedirectResponse("/admin/notifications", status_code=303)


@app.get("/admin/analytics", response_class=HTMLResponse)
def admin_analytics():
    return open(os.path.join(FRONTEND_DIR, "admin_analytics.html"), encoding="utf-8").read()


@app.get("/admin/preview", response_class=HTMLResponse)
def admin_preview():
    return open(os.path.join(FRONTEND_DIR, "admin_preview.html"), encoding="utf-8").read()

#user side bar
# ================= USER SIDEBAR ROUTES =================

@app.get("/user/tutorials/saved", response_class=HTMLResponse)
def user_saved_tutorials(email: str):
    user = get_user_by_email(email)
    if not user:
        return RedirectResponse("/user/login")

    user_id, name, email, level, xp, challenges, rank = user

    html = open(
        os.path.join(FRONTEND_DIR, "user_saved_tutorials.html"),
        encoding="utf-8"
    ).read()

    return HTMLResponse(
        html.replace("{{USERNAME}}", name)
            .replace("{{EMAIL}}", email)
            .replace("{{LEVEL}}", str(level))
    )


@app.get("/user/challenges/daily", response_class=HTMLResponse)
def user_daily_challenge(email: str | None = None):
    if not email:
        return RedirectResponse("/user/login")

    user = get_user_by_email(email)
    if not user:
        return RedirectResponse("/user/login")

    user_id, name, email, level, *_ = user

    html = open(
        os.path.join(FRONTEND_DIR, "user_daily_challenges.html"),
        encoding="utf-8"
    ).read()

    return HTMLResponse(
        html.replace("{{EMAIL}}", email)
            .replace("{{USERNAME}}", name)
            .replace("{{LEVEL}}", str(level))
    )

@app.get("/user/api/attempt-history")
def get_attempt_history(email: str):
    """Fetch attempt history for a user"""
    user = get_user_by_email(email)
    if not user:
        return JSONResponse({"error": "User not found"}, status_code=404)
    
    user_id = user[0]
    
    conn = get_db()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT id, activity_type, resource_name, resource_type, visited_at
        FROM attempt_history
        WHERE user_id = ?
        ORDER BY visited_at DESC
        LIMIT 100
    """, (user_id,))
    
    attempts = []
    for row in cur.fetchall():
        attempts.append({
            "id": row[0],
            "activity_type": row[1],
            "resource_name": row[2],
            "resource_type": row[3],
            "visited_at": row[4]
        })
    
    conn.close()
    return {"attempts": attempts}

@app.delete("/user/api/attempt-history/{attempt_id}")
def delete_attempt(attempt_id: int, email: str):
    """Delete a single attempt history record"""
    user = get_user_by_email(email)
    if not user:
        return JSONResponse({"error": "User not found"}, status_code=404)
    
    user_id = user[0]
    
    conn = get_db()
    cur = conn.cursor()
    
    # Verify the record belongs to this user
    cur.execute("""
        SELECT user_id FROM attempt_history WHERE id = ?
    """, (attempt_id,))
    
    result = cur.fetchone()
    if not result or result[0] != user_id:
        conn.close()
        return JSONResponse({"error": "Record not found or unauthorized"}, status_code=403)
    
    # Delete the record
    cur.execute("DELETE FROM attempt_history WHERE id = ?", (attempt_id,))
    conn.commit()
    conn.close()
    
    return {"success": True, "message": "Attempt deleted successfully"}

@app.delete("/user/api/attempt-history")
def delete_all_attempts(email: str):
    """Delete all attempt history records for a user"""
    user = get_user_by_email(email)
    if not user:
        return JSONResponse({"error": "User not found"}, status_code=404)
    
    user_id = user[0]
    
    conn = get_db()
    cur = conn.cursor()
    
    # Count before deletion
    cur.execute("SELECT COUNT(*) FROM attempt_history WHERE user_id = ?", (user_id,))
    count = cur.fetchone()[0]
    
    # Delete all records
    cur.execute("DELETE FROM attempt_history WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    
    return {"success": True, "message": f"Deleted {count} attempt history records"}

@app.get("/user/attempt-history", response_class=HTMLResponse)
def user_attempt_history(email: str):
    user = get_user_by_email(email)
    if not user:
        return RedirectResponse("/user/login")

    user_id, name, email, level, xp, challenges, rank = user

    html = open(
        os.path.join(FRONTEND_DIR, "user_attempt_history.html"),
        encoding="utf-8"
    ).read()

    return HTMLResponse(
        html.replace("{{EMAIL}}", email)
            .replace("{{USERNAME}}", name)
            .replace("{{LEVEL}}", str(level))
    )

@app.get("/user/leaderboard", response_class=HTMLResponse)
def user_leaderboard(email: str):
    user = get_user_by_email(email)
    if not user:
        return RedirectResponse("/user/login")

    user_id, username, email, level, xp, challenges, rank = user

    conn = get_db()
    cur = conn.cursor()

    # 1️⃣ Calculate scores based on CHALLENGE XP + COMPLETED TUTORIALS
    cur.execute("""
        SELECT u.id, u.name, u.email, u.level, u.challenges_solved,
               u.xp + COALESCE(SUM(t.points), 0) as total_score
        FROM users u
        LEFT JOIN tutorial_progress tp ON u.name = tp.username AND tp.completed = 1
        LEFT JOIN tutorials t ON tp.tutorial_id = t.id
        GROUP BY u.id
        ORDER BY total_score DESC, u.level DESC, u.id ASC
    """)
    users_with_scores = cur.fetchall()
    
    # 2️⃣ Update global_rank based on total_score (CHALLENGE XP + TUTORIAL POINTS)
    for rank, (uid, _, _, _, _, _) in enumerate(users_with_scores, start=1):
        cur.execute(
            "UPDATE users SET global_rank=? WHERE id=?",
            (rank, uid)
        )
    
    conn.commit()
    
    # 3️⃣ Fetch users WITH updated global_rank and calculated total_score (xp already includes tutorials)
    cur.execute("""
        SELECT u.name, u.email, u.level, u.challenges_solved, u.global_rank, u.xp as total_score
        FROM users u
        ORDER BY u.global_rank ASC
    """)
    ranked_users = cur.fetchall()

    conn.close()

    # ---------------- PODIUM DEFAULTS ----------------
    first = second = third = {
        "name": "—",
        "score": 0,
        "solved": 0,
        "email": "—"
    }

    for name, email_u, lvl, solved, rnk, total_score in ranked_users:
        data = {
            "name": name,
            "score": total_score,
            "solved": solved,
            "email": email_u
        }
        if rnk == 1:
            first = data
        elif rnk == 2:
            second = data
        elif rnk == 3:
            third = data

    # ---------------- TABLE ROWS ----------------
    rows = ""
    for name, email_u, lvl, solved, rnk, total_score in ranked_users:
        medal = ""
        if rnk == 1:
            medal = "🥇"
        elif rnk == 2:
            medal = "🥈"
        elif rnk == 3:
            medal = "🥉"

        rows += f"""
        <tr>
            <td>{medal} #{rnk}</td>
            <td>{name}</td>
            <td>{lvl}</td>
            <td class="score">{total_score}</td>
            <td>{solved}</td>
        </tr>
        """

    html = open(
        os.path.join(FRONTEND_DIR, "user_leaderboard.html"),
        encoding="utf-8"
    ).read()

    return HTMLResponse(
        html.replace("{{USERNAME}}", username)
            .replace("{{EMAIL}}", email)
            .replace("{{LEVEL}}", str(level))
            .replace("{{LEADERBOARD_ROWS}}", rows)

            # 🥇 FIRST
            .replace("{{FIRST_NAME}}", first["name"])
            .replace("{{FIRST_SCORE}}", str(first["score"]))
            .replace("{{FIRST_SOLVED}}", str(first["solved"]))
            .replace("{{FIRST_EMAIL}}", first["email"])

            # 🥈 SECOND
            .replace("{{SECOND_NAME}}", second["name"])
            .replace("{{SECOND_SCORE}}", str(second["score"]))
            .replace("{{SECOND_SOLVED}}", str(second["solved"]))
            .replace("{{SECOND_EMAIL}}", second["email"])

            # 🥉 THIRD
            .replace("{{THIRD_NAME}}", third["name"])
            .replace("{{THIRD_SCORE}}", str(third["score"]))
            .replace("{{THIRD_SOLVED}}", str(third["solved"]))
            .replace("{{THIRD_EMAIL}}", third["email"])
    )


@app.get("/user/achievements", response_class=HTMLResponse)
def user_achievements(email: str):
    user = get_user_by_email(email)
    if not user:
        return RedirectResponse("/user/login")

    user_id, name, email, level, xp, challenges_solved, rank = user

    # -------- ACHIEVEMENT CONDITIONS --------
    achievements = {
        "FIRST_BLOOD": challenges_solved >= 1,
        "SPEED_DEMON": challenges_solved >= 2,
        "SQL_MASTER": challenges_solved >= 10,
        "WEEK_WARRIOR": level >= 5
    }

    ACH_TOTAL = len(achievements)
    ACH_UNLOCKED = sum(1 for a in achievements.values() if a)

    TOTAL_POINTS = challenges_solved * 10
    COMPLETION_RATE = int((ACH_UNLOCKED / ACH_TOTAL) * 100) if ACH_TOTAL else 0

    def status(val):
        return "Unlocked" if val else "Locked"

    def css_class(val):
        return "" if val else "locked"

    def format_date(val):
        return "Today" if val else "Locked"

    html = open(
        os.path.join(FRONTEND_DIR, "user_achievements.html"),
        encoding="utf-8"
    ).read()

    return HTMLResponse(
        html
        .replace("{{EMAIL}}", email)
        .replace("{{USERNAME}}", name)
        .replace("{{LEVEL}}", str(level))

        .replace("{{ACH_UNLOCKED}}", str(ACH_UNLOCKED))
        .replace("{{ACH_TOTAL}}", str(ACH_TOTAL))
        .replace("{{TOTAL_POINTS}}", str(TOTAL_POINTS))
        .replace("{{COMPLETION_RATE}}", str(COMPLETION_RATE))

        .replace("{{FIRST_BLOOD_STATUS}}", status(achievements["FIRST_BLOOD"]))
        .replace("{{FIRST_BLOOD_CLASS}}", css_class(achievements["FIRST_BLOOD"]))
        .replace("{{FIRST_BLOOD_BADGE}}", css_class(achievements["FIRST_BLOOD"]))
        .replace("{{FIRST_BLOOD_DATE}}", format_date(achievements["FIRST_BLOOD"]))

        .replace("{{SPEED_DEMON_STATUS}}", status(achievements["SPEED_DEMON"]))
        .replace("{{SPEED_DEMON_CLASS}}", css_class(achievements["SPEED_DEMON"]))
        .replace("{{SPEED_DEMON_BADGE}}", css_class(achievements["SPEED_DEMON"]))
        .replace("{{SPEED_DEMON_DATE}}", format_date(achievements["SPEED_DEMON"]))

        .replace("{{SQL_MASTER_STATUS}}", status(achievements["SQL_MASTER"]))
        .replace("{{SQL_MASTER_CLASS}}", css_class(achievements["SQL_MASTER"]))
        .replace("{{SQL_MASTER_BADGE}}", css_class(achievements["SQL_MASTER"]))
        .replace("{{SQL_MASTER_DATE}}", format_date(achievements["SQL_MASTER"]))

        .replace("{{WEEK_WARRIOR_STATUS}}", status(achievements["WEEK_WARRIOR"]))
        .replace("{{WEEK_WARRIOR_CLASS}}", css_class(achievements["WEEK_WARRIOR"]))
        .replace("{{WEEK_WARRIOR_BADGE}}", css_class(achievements["WEEK_WARRIOR"]))
        .replace("{{WEEK_WARRIOR_DATE}}", format_date(achievements["WEEK_WARRIOR"]))
    )

@app.get("/user/recent", response_class=HTMLResponse)
def user_recently_viewed(email: str):
    user = get_user_by_email(email)
    if not user:
        return RedirectResponse("/user/login")

    name, email, level, *_ = user

    html = open(
        os.path.join(FRONTEND_DIR, "user_recently_viewed.html"),
        encoding="utf-8"
    ).read()

    return HTMLResponse(
        html.replace("{{EMAIL}}", email)
            .replace("{{USERNAME}}", name)
            .replace("{{LEVEL}}", str(level))
    )

#admin tutorials
@app.get("/admin/tutorials", response_class=HTMLResponse)
def admin_tutorials():
    return open(
        os.path.join(FRONTEND_DIR, "admin_tutorials.html"),
        encoding="utf-8"
    ).read()

#admin create tutorials
@app.post("/admin/tutorials/create")
def create_tutorial(payload: dict):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO tutorials
        (title, category, difficulty, estimated_time, intro, points)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        payload["title"],
        payload.get("category"),
        payload.get("difficulty"),
        payload.get("estimated_time"),
        payload.get("intro"),
        payload.get("points", 0)
    ))

    tutorial_id = cur.lastrowid
    conn.commit()
    conn.close()

    return {"id": tutorial_id}

# ================== ADMIN QUIZ ENDPOINTS ==================
@app.get("/admin/quizzes", response_class=HTMLResponse)
def admin_quizzes():
    return open(
        os.path.join(FRONTEND_DIR, "admin_quizzes.html"),
        encoding="utf-8"
    ).read()

@app.get("/admin/api/quizzes")
def get_admin_quizzes(email: str):
    """Get all quizzes for admin dashboard"""
    conn = get_db()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT id, title, category, difficulty, points, is_published, created_at
        FROM quizzes
        ORDER BY id ASC
    """)
    
    quizzes = cur.fetchall()
    conn.close()
    
    return [
        {
            "id": q[0],
            "title": q[1],
            "category": q[2],
            "difficulty": q[3],
            "points": q[4],
            "is_published": bool(q[5]),
            "created_at": q[6]
        }
        for q in quizzes
    ]

@app.post("/admin/quizzes/create")
def create_quiz(payload: dict = Body(...)):
    """Create a new quiz"""
    conn = get_db()
    cur = conn.cursor()
    
    try:
        cur.execute("""
            INSERT INTO quizzes
            (title, description, category, difficulty, points, passing_score, time_limit, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            payload.get("title"),
            payload.get("description"),
            payload.get("category"),
            payload.get("difficulty"),
            payload.get("points", 10),
            payload.get("passing_score", 70),
            payload.get("time_limit"),
            payload.get("created_by")
        ))
        
        quiz_id = cur.lastrowid
        conn.commit()
        conn.close()
        
        return {"id": quiz_id, "title": payload.get("title")}
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/admin/quizzes/{quiz_id}/publish")
def publish_quiz(quiz_id: int):
    """Publish a quiz to make it visible to users"""
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE quizzes SET is_published = 1 WHERE id = ?", (quiz_id,))
        conn.commit()
        conn.close()
        return {"success": True, "message": "Quiz published successfully"}
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/admin/quizzes/{quiz_id}/unpublish")
def unpublish_quiz(quiz_id: int):
    """Unpublish a quiz to hide it from users"""
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE quizzes SET is_published = 0 WHERE id = ?", (quiz_id,))
        conn.commit()
        conn.close()
        return {"success": True, "message": "Quiz unpublished successfully"}
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/admin/quizzes/{quiz_id}/edit", response_class=HTMLResponse)
def edit_quiz_page(quiz_id: int):
    """Get quiz edit page"""
    return open(
        os.path.join(FRONTEND_DIR, "admin_quiz_editor.html"),
        encoding="utf-8"
    ).read().replace("{{QUIZ_ID}}", str(quiz_id))

@app.get("/user/api/quizzes/{quiz_id}")
def get_user_quiz_details(quiz_id: int, email: str = None, preview: str = "false"):
    """Get detailed quiz information - works for both users and admin previews"""
    conn = get_db()
    cur = conn.cursor()
    
    # Check if it's an admin preview request
    if preview.lower() == "true" and email:
        cur.execute("SELECT email FROM admin WHERE email = ?", (email,))
        if not cur.fetchone():
            conn.close()
            raise HTTPException(status_code=403, detail="Unauthorized")
    
    # Get quiz info
    cur.execute("""
        SELECT id, title, description, category, difficulty, points, passing_score, 
               time_limit, is_published, created_at
        FROM quizzes
        WHERE id = ?
    """, (quiz_id,))
    
    quiz = cur.fetchone()
    if not quiz:
        conn.close()
        raise HTTPException(status_code=404, detail="Quiz not found")
    
    # Check if quiz is published or if it's a preview request
    if not quiz[8] and preview.lower() != "true":
        conn.close()
        raise HTTPException(status_code=404, detail="Quiz not found")
    
    # Get questions
    cur.execute("""
        SELECT id, question_text, question_type, question_order, points_value
        FROM quiz_questions
        WHERE quiz_id = ?
        ORDER BY question_order
    """, (quiz_id,))
    
    questions = cur.fetchall()
    
    questions_list = []
    for q in questions:
        # Get options for each question
        cur.execute("""
            SELECT id, option_text, is_correct, option_order
            FROM quiz_options
            WHERE question_id = ?
            ORDER BY option_order
        """, (q[0],))
        
        options = cur.fetchall()
        
        questions_list.append({
            "id": q[0],
            "text": q[1],
            "type": q[2],
            "order": q[3],
            "points": q[4],
            "options": [
                {
                    "id": opt[0],
                    "text": opt[1],
                    "is_correct": bool(opt[2]),
                    "order": opt[3]
                }
                for opt in options
            ]
        })
    
    conn.close()
    
    return {
        "id": quiz[0],
        "title": quiz[1],
        "description": quiz[2],
        "category": quiz[3],
        "difficulty": quiz[4],
        "points": quiz[5],
        "passing_score": quiz[6],
        "time_limit": quiz[7],
        "is_published": bool(quiz[8]),
        "created_at": quiz[9],
        "questions": questions_list
    }

@app.get("/admin/api/quizzes/{quiz_id}")
def get_quiz_details(quiz_id: int):
    """Get detailed quiz information with questions and options"""
    conn = get_db()
    cur = conn.cursor()
    
    # Get quiz info
    cur.execute("""
        SELECT id, title, description, category, difficulty, points, passing_score, 
               time_limit, is_published, created_at
        FROM quizzes
        WHERE id = ?
    """, (quiz_id,))
    
    quiz = cur.fetchone()
    if not quiz:
        conn.close()
        raise HTTPException(status_code=404, detail="Quiz not found")
    
    # Get questions
    cur.execute("""
        SELECT id, question_text, question_type, question_order, points_value
        FROM quiz_questions
        WHERE quiz_id = ?
        ORDER BY question_order
    """, (quiz_id,))
    
    questions = cur.fetchall()
    
    questions_list = []
    for q in questions:
        # Get options for each question
        cur.execute("""
            SELECT id, option_text, is_correct, option_order
            FROM quiz_options
            WHERE question_id = ?
            ORDER BY option_order
        """, (q[0],))
        
        options = cur.fetchall()
        
        questions_list.append({
            "id": q[0],
            "text": q[1],
            "type": q[2],
            "order": q[3],
            "points": q[4],
            "options": [
                {
                    "id": opt[0],
                    "text": opt[1],
                    "is_correct": bool(opt[2]),
                    "order": opt[3]
                }
                for opt in options
            ]
        })
    
    conn.close()
    
    return {
        "id": quiz[0],
        "title": quiz[1],
        "description": quiz[2],
        "category": quiz[3],
        "difficulty": quiz[4],
        "points": quiz[5],
        "passing_score": quiz[6],
        "time_limit": quiz[7],
        "is_published": bool(quiz[8]),
        "created_at": quiz[9],
        "questions": questions_list
    }

@app.post("/admin/api/quizzes/{quiz_id}/save_all")
def save_quiz_all(quiz_id: int, payload: dict = Body(...)):
    """Global save for quiz questions and options"""
    conn = get_db()
    cur = conn.cursor()
    try:
        questions_data = payload.get("questions", [])
        logger.info(f"DEBUG: Saving quiz {quiz_id} with {len(questions_data)} questions")
        
        # Get existing question IDs to identify deletions
        cur.execute("SELECT id FROM quiz_questions WHERE quiz_id = ?", (quiz_id,))
        existing_q_ids = {row[0] for row in cur.fetchall()}
        processed_q_ids = set()

        for q_index, q_data in enumerate(questions_data):
            q_id = q_data.get("id")
            
            # Robust ID handling (convert string IDs to int if they represent numbers)
            if q_id is not None:
                try:
                    q_id_str = str(q_id)
                    if q_id_str.startswith('new_'):
                        q_id = None
                    else:
                        q_id = int(q_id_str)
                except (ValueError, TypeError):
                    q_id = None

            q_text = q_data.get("text", "")
            q_type = q_data.get("type", "multiple_choice")
            q_points = q_data.get("points", 1)
            
            if q_id and q_id in existing_q_ids:
                # Update existing question
                logger.info(f"DEBUG: Updating question {q_id}: {q_text[:30]}...")
                cur.execute("""
                    UPDATE quiz_questions 
                    SET question_text = ?, question_type = ?, question_order = ?, points_value = ?
                    WHERE id = ? AND quiz_id = ?
                """, (q_text, q_type, q_index, q_points, q_id, quiz_id))
                processed_q_ids.add(q_id)
            else:
                # Insert new question
                cur.execute("""
                    INSERT INTO quiz_questions (quiz_id, question_text, question_type, question_order, points_value)
                    VALUES (?, ?, ?, ?, ?)
                """, (quiz_id, q_text, q_type, q_index, q_points))
                q_id = cur.lastrowid
                processed_q_ids.add(q_id)

            # Handle Options for this question
            options_data = q_data.get("options", [])
            cur.execute("SELECT id FROM quiz_options WHERE question_id = ?", (q_id,))
            existing_opt_ids = {row[0] for row in cur.fetchall()}
            processed_opt_ids = set()

            for opt_index, opt_data in enumerate(options_data):
                opt_id = opt_data.get("id")
                
                # Robust ID handling for options
                if opt_id is not None:
                    try:
                        opt_id_str = str(opt_id)
                        if opt_id_str.startswith('new_'):
                            opt_id = None
                        else:
                            opt_id = int(opt_id_str)
                    except (ValueError, TypeError):
                        opt_id = None

                opt_text = opt_data.get("text", "")
                is_correct = 1 if opt_data.get("is_correct") else 0
                
                if opt_id and opt_id in existing_opt_ids:
                    # Update
                    cur.execute("""
                        UPDATE quiz_options 
                        SET option_text = ?, is_correct = ?, option_order = ?
                        WHERE id = ? AND question_id = ?
                    """, (opt_text, is_correct, opt_index, opt_id, q_id))
                    processed_opt_ids.add(opt_id)
                else:
                    # Insert
                    cur.execute("""
                        INSERT INTO quiz_options (question_id, option_text, is_correct, option_order)
                        VALUES (?, ?, ?, ?)
                    """, (q_id, opt_text, is_correct, opt_index))
                    processed_opt_ids.add(cur.lastrowid)

            # Delete removed options
            for old_opt_id in existing_opt_ids - processed_opt_ids:
                cur.execute("DELETE FROM quiz_options WHERE id = ? AND question_id = ?", (old_opt_id, q_id))

        # Delete removed questions
        deleted_count = 0
        for old_q_id in existing_q_ids - processed_q_ids:
            cur.execute("DELETE FROM quiz_questions WHERE id = ? AND quiz_id = ?", (old_q_id, quiz_id))
            deleted_count += 1
        
        if deleted_count > 0:
            print(f"DEBUG: Deleted {deleted_count} abandoned questions")

        conn.commit()
        return {"success": True, "message": "Quiz saved successfully", "question_count": len(processed_q_ids)}
    except Exception as e:
        conn.rollback()
        print(f"Error saving quiz: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.post("/admin/quizzes/{quiz_id}/update")
def update_quiz(quiz_id: int, payload: dict = Body(...)):
    """Update quiz details"""
    conn = get_db()
    cur = conn.cursor()
    
    try:
        cur.execute("""
            UPDATE quizzes
            SET title = ?, description = ?, category = ?, difficulty = ?,
                points = ?, passing_score = ?, time_limit = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            payload.get("title"),
            payload.get("description"),
            payload.get("category"),
            payload.get("difficulty"),
            payload.get("points", 10),
            payload.get("passing_score", 70),
            payload.get("time_limit"),
            quiz_id
        ))
        
        conn.commit()
        conn.close()
        
        return {"success": True, "message": "Quiz updated"}
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/admin/quizzes/{quiz_id}/questions/add")
def add_quiz_question(quiz_id: int, payload: dict = Body(...)):
    """Add a question to a quiz"""
    conn = get_db()
    cur = conn.cursor()
    
    try:
        # Get max order
        cur.execute("SELECT MAX(question_order) FROM quiz_questions WHERE quiz_id = ?", (quiz_id,))
        max_order = cur.fetchone()[0] or 0
        
        cur.execute("""
            INSERT INTO quiz_questions
            (quiz_id, question_text, question_type, question_order, points_value)
            VALUES (?, ?, ?, ?, ?)
        """, (
            quiz_id,
            payload.get("question_text"),
            payload.get("question_type", "multiple_choice"),
            max_order + 1,
            payload.get("points_value", 1)
        ))
        
        question_id = cur.lastrowid
        conn.commit()
        conn.close()
        
        return {"id": question_id, "order": max_order + 1}
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/admin/quizzes/{quiz_id}/questions/{question_id}/update")
def update_quiz_question(quiz_id: int, question_id: int, payload: dict = Body(...)):
    """Update a quiz question"""
    conn = get_db()
    cur = conn.cursor()
    
    try:
        cur.execute("""
            UPDATE quiz_questions
            SET question_text = ?, question_type = ?, points_value = ?
            WHERE id = ? AND quiz_id = ?
        """, (
            payload.get("question_text"),
            payload.get("question_type"),
            payload.get("points_value", 1),
            question_id,
            quiz_id
        ))
        
        conn.commit()
        conn.close()
        
        return {"success": True, "message": "Question updated"}
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/admin/quizzes/{quiz_id}/questions/{question_id}")
def delete_quiz_question(quiz_id: int, question_id: int):
    """Delete a quiz question"""
    conn = get_db()
    cur = conn.cursor()
    
    try:
        cur.execute("DELETE FROM quiz_questions WHERE id = ? AND quiz_id = ?", (question_id, quiz_id))
        conn.commit()
        conn.close()
        
        return {"success": True, "message": "Question deleted"}
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/admin/quizzes/{quiz_id}/questions/{question_id}/options/add")
def add_quiz_option(quiz_id: int, question_id: int, payload: dict = Body(...)):
    """Add an option to a quiz question"""
    conn = get_db()
    cur = conn.cursor()
    
    try:
        # Verify question belongs to quiz
        cur.execute("SELECT id FROM quiz_questions WHERE id = ? AND quiz_id = ?", (question_id, quiz_id))
        if not cur.fetchone():
            conn.close()
            raise HTTPException(status_code=404, detail="Question not found")
        
        # Get max order
        cur.execute("SELECT MAX(option_order) FROM quiz_options WHERE question_id = ?", (question_id,))
        max_order = cur.fetchone()[0] or 0
        
        cur.execute("""
            INSERT INTO quiz_options
            (question_id, option_text, is_correct, option_order)
            VALUES (?, ?, ?, ?)
        """, (
            question_id,
            payload.get("option_text"),
            payload.get("is_correct", 0),
            max_order + 1
        ))
        
        option_id = cur.lastrowid
        conn.commit()
        conn.close()
        
        return {"id": option_id, "order": max_order + 1}
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail=str(e))

@app.put("/admin/quizzes/{quiz_id}/questions/{question_id}/options/{option_id}")
def update_quiz_option(quiz_id: int, question_id: int, option_id: int, payload: dict = Body(...)):
    """Update a quiz option"""
    conn = get_db()
    cur = conn.cursor()
    
    try:
        cur.execute("""
            UPDATE quiz_options
            SET option_text = ?, is_correct = ?
            WHERE id = ? AND question_id = ?
        """, (
            payload.get("option_text"),
            payload.get("is_correct", 0),
            option_id,
            question_id
        ))
        
        conn.commit()
        conn.close()
        
        return {"success": True, "message": "Option updated"}
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/admin/quizzes/{quiz_id}/questions/{question_id}/options/{option_id}")
def delete_quiz_option(quiz_id: int, question_id: int, option_id: int):
    """Delete a quiz option"""
    conn = get_db()
    cur = conn.cursor()
    
    try:
        cur.execute("DELETE FROM quiz_options WHERE id = ? AND question_id = ?", (option_id, question_id))
        conn.commit()
        conn.close()
        
        return {"success": True, "message": "Option deleted"}
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/user/quizzes", response_class=HTMLResponse)
def user_quizzes_page(email: str):
    user = get_user_by_email(email)
    if not user:
        return RedirectResponse("/user/login")
    
    user_id, name, email, level, xp, solved, rank = user
    html = open(os.path.join(FRONTEND_DIR, "user_quizzes.html"), encoding="utf-8").read()
    return HTMLResponse(
        html.replace("{{EMAIL}}", email)
            .replace("{{USERNAME}}", name)
            .replace("{{LEVEL}}", str(level))
    )

@app.post("/user/api/quizzes/submit")
def user_submit_quiz(payload: dict = Body(...)):
    """Handle quiz submission, calculate score, and award XP"""
    email = payload.get("email")
    quiz_id = payload.get("quizId")
    user_answers = payload.get("userAnswers")  # {question_id: selected_option_id}
    
    print(f"DEBUG [QUIZ SUBMIT]: Email={email}, QuizID={quiz_id}")
    
    if not email or not quiz_id:
        raise HTTPException(status_code=400, detail="Missing email or quizId")

    conn = get_db()
    cur = conn.cursor()
    
    try:
        # Get user
        cur.execute("SELECT id, xp FROM users WHERE email = ?", (email,))
        user = cur.fetchone()
        if not user:
            conn.close()
            raise HTTPException(status_code=404, detail="User not found")
        user_id, current_xp = user
        print(f"DEBUG [QUIZ SUBMIT]: Found user_id={user_id}")

        # Get quiz details
        cur.execute("SELECT points, passing_score FROM quizzes WHERE id = ?", (quiz_id,))
        quiz = cur.fetchone()
        if not quiz:
            conn.close()
            raise HTTPException(status_code=404, detail="Quiz not found")
        quiz_points, passing_score = quiz
        quiz_points = int(quiz_points) if quiz_points else 0
        passing_score = int(passing_score) if passing_score else 70
        print(f"DEBUG [QUIZ SUBMIT]: Quiz points={quiz_points}, passing_score={passing_score}")

        # Get questions and options to verify answers
        cur.execute("SELECT id FROM quiz_questions WHERE quiz_id = ?", (quiz_id,))
        questions = cur.fetchall()
        total_questions = len(questions)
        print(f"DEBUG [QUIZ SUBMIT]: Total questions={total_questions}")
        
        if total_questions == 0:
            conn.close()
            return {"success": True, "percentage": 100, "passed": True, "xp_earned": 0}

        correct_count = 0
        for q_tuple in questions:
            q_id = q_tuple[0]
            cur.execute("SELECT id FROM quiz_options WHERE question_id = ? AND is_correct = 1", (q_id,))
            correct_opt = cur.fetchone()
            
            if correct_opt and str(q_id) in user_answers:
                if int(user_answers[str(q_id)]) == correct_opt[0]:
                    correct_count += 1

        percentage = round((correct_count / total_questions) * 100)
        passed = 1 if percentage >= passing_score else 0
        xp_earned = int(quiz_points) if passed else 0
        print(f"DEBUG [QUIZ SUBMIT]: correct_count={correct_count}, total={total_questions}, percentage={percentage}, passed={passed}, xp_earned={xp_earned}")

        # Check if user already passed this quiz BEFORE recording new attempt
        cur.execute("SELECT id FROM quiz_attempts WHERE user_id = ? AND quiz_id = ? AND passed = 1", (user_id, quiz_id))
        already_passed = cur.fetchone() is not None
        print(f"DEBUG [QUIZ SUBMIT]: already_passed={already_passed}")

        # Record attempt
        cur.execute("""
            INSERT INTO quiz_attempts (user_id, quiz_id, score, total_questions, percentage, passed)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, quiz_id, correct_count, total_questions, percentage, passed))
        
        attempt_id = cur.lastrowid
        print(f"DEBUG [QUIZ SUBMIT]: Inserted attempt_id={attempt_id} with passed={passed}")

        # Update user XP if passed for the first time
        if passed and not already_passed:
            # Award XP: fetch current XP and add the earned XP explicitly
            cur.execute("SELECT xp FROM users WHERE id = ?", (user_id,))
            current_xp_record = cur.fetchone()
            current_xp_val = int(current_xp_record[0]) if current_xp_record and current_xp_record[0] else 0
            new_xp = current_xp_val + xp_earned
            cur.execute("UPDATE users SET xp = ? WHERE id = ?", (new_xp, user_id))
            print(f"DEBUG [QUIZ SUBMIT]: Updated user XP: {current_xp_val} -> {new_xp}")
        elif passed and already_passed:
            # Quiz already passed, do not award points again
            xp_earned = 0
            print(f"DEBUG [QUIZ SUBMIT]: Quiz already passed, no XP awarded")

        # ✅ Commit changes to database
        conn.commit()
        print(f"DEBUG [QUIZ SUBMIT]: Changes committed to database")

        # ✅ Record in unified attempt_history
        # Fetch quiz title for the history record
        cur.execute("SELECT title FROM quizzes WHERE id = ?", (quiz_id,))
        qz_title = cur.fetchone()[0]
        
        conn.close() # Close current connection after committing
        
        log_attempt(
            user_id=user_id,
            activity_type="completed" if passed else "failed",
            resource_id=quiz_id,
            resource_name=qz_title,
            resource_type="quiz"
        )

        return {
            "success": True,
            "percentage": percentage,
            "passed": bool(passed),
            "xp_earned": xp_earned,
            "total_questions": total_questions,
            "correct_count": correct_count
        }
    except Exception as e:
        if conn: conn.close()
        logger.error(f"Error submitting quiz: {e}")
        print(f"DEBUG [QUIZ SUBMIT ERROR]: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/user/api/quizzes")
def user_api_quizzes(email: str):
    user = get_user_by_email(email)
    if not user:
        return {"success": False, "quizzes": []}
    
    user_id = user[0]
    conn = get_db()
    cur = conn.cursor()
    
    # Get all published quizzes ordered by ID
    cur.execute("SELECT id, title, description, category, difficulty, points, time_limit FROM quizzes WHERE is_published = 1 ORDER BY id ASC")
    quizzes = cur.fetchall()
    
    # Get the highest quiz ID that the user has passed
    cur.execute("""
        SELECT quiz_id FROM quiz_attempts 
        WHERE user_id = ? AND passed = 1 
        ORDER BY quiz_id DESC 
        LIMIT 1
    """, (user_id,))
    highest_passed = cur.fetchone()
    highest_passed_id = highest_passed[0] if highest_passed else None
    
    conn.close()
    
    quiz_list = []
    for idx, r in enumerate(quizzes):
        quiz_id = r[0]
        
        # Determine if quiz is locked
        if highest_passed_id is None:
            # No quizzes passed yet, only first quiz is unlocked
            is_locked = (idx > 0)
        else:
            # Quiz is unlocked if user has passed a quiz with ID >= current quiz ID - 1
            # In other words: unlock the quiz whose prerequisite (previous quiz) was passed
            is_locked = quiz_id > highest_passed_id + 1
        
        quiz_list.append({
            "id": quiz_id,
            "title": r[1],
            "description": r[2],
            "category": r[3],
            "difficulty": r[4],
            "points": r[5],
            "time_limit": r[6],
            "is_locked": is_locked
        })
    
    return {
        "success": True,
        "quizzes": quiz_list
    }

@app.get("/user/quizzes/{quiz_id}", response_class=HTMLResponse)
def user_quiz_take_page(quiz_id: int, email: str, preview: str = "false"):
    conn = get_db()
    cur = conn.cursor()
    
    # Check if quiz exists
    cur.execute("SELECT id, is_published FROM quizzes WHERE id = ?", (quiz_id,))
    quiz = cur.fetchone()
    if not quiz:
        conn.close()
        raise HTTPException(status_code=404, detail="Quiz not found")
    
    quiz_id_db, is_published = quiz
    
    # If it's a preview request, allow admin or user to preview
    if preview.lower() == "true":
        # Try to get admin - if found, allow preview
        try:
            cur.execute("SELECT email FROM admin WHERE email = ?", (email,))
            admin = cur.fetchone()
            if admin:
                conn.close()
                html = open(os.path.join(FRONTEND_DIR, "user_quiz_take.html"), encoding="utf-8").read()
                return HTMLResponse(
                    html.replace("{{EMAIL}}", email)
                        .replace("{{USERNAME}}", "Admin")
                        .replace("{{LEVEL}}", "0")
                        .replace("{{QUIZ_ID}}", str(quiz_id))
                )
        except:
            # Admin table may not exist or other issue, continue to user check
            pass
        
        # If not admin, check if user exists
        user = get_user_by_email(email)
        if user:
            conn.close()
            user_id, name, user_email, level, xp, solved, rank = user
            html = open(os.path.join(FRONTEND_DIR, "user_quiz_take.html"), encoding="utf-8").read()
            return HTMLResponse(
                html.replace("{{EMAIL}}", user_email)
                    .replace("{{USERNAME}}", name)
                    .replace("{{LEVEL}}", str(level))
                    .replace("{{QUIZ_ID}}", str(quiz_id))
            )
        else:
            # Preview request but user not found - redirect to login
            conn.close()
            return RedirectResponse(f"/user/login?email={quote(email)}&next=/user/quizzes/{quiz_id}?preview=true", status_code=302)
    
    # For regular users, check if quiz is published
    if not is_published:
        conn.close()
        raise HTTPException(status_code=404, detail="Quiz not found")
    
    # Check if user exists in users table
    user = get_user_by_email(email)
    if not user:
        conn.close()
        return RedirectResponse("/user/login")
    
    conn.close()
    user_id, name, email, level, xp, solved, rank = user
    html = open(os.path.join(FRONTEND_DIR, "user_quiz_take.html"), encoding="utf-8").read()
    return HTMLResponse(
        html.replace("{{EMAIL}}", email)
            .replace("{{USERNAME}}", name)
            .replace("{{LEVEL}}", str(level))
            .replace("{{QUIZ_ID}}", str(quiz_id))
    )
def publish_quiz(quiz_id: int):
    """Publish a quiz"""
    conn = get_db()
    cur = conn.cursor()
    
    try:
        # Verify quiz has at least one question
        cur.execute("SELECT COUNT(*) FROM quiz_questions WHERE quiz_id = ?", (quiz_id,))
        if cur.fetchone()[0] == 0:
            conn.close()
            raise HTTPException(status_code=400, detail="Quiz must have at least one question")
        
        cur.execute("UPDATE quizzes SET is_published = 1 WHERE id = ?", (quiz_id,))
        conn.commit()
        conn.close()
        
        return {"success": True, "message": "Quiz published"}
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/admin/quizzes/{quiz_id}/unpublish")
def unpublish_quiz(quiz_id: int):
    """Unpublish a quiz"""
    conn = get_db()
    cur = conn.cursor()
    
    try:
        cur.execute("UPDATE quizzes SET is_published = 0 WHERE id = ?", (quiz_id,))
        conn.commit()
        conn.close()
        
        return {"success": True, "message": "Quiz unpublished"}
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/admin/quizzes/{quiz_id}")
def delete_quiz(quiz_id: int):
    """Delete a quiz"""
    conn = get_db()
    cur = conn.cursor()
    
    try:
        cur.execute("DELETE FROM quizzes WHERE id = ?", (quiz_id,))
        conn.commit()
        conn.close()
        
        return {"success": True, "message": "Quiz deleted"}
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail=str(e))

UPLOAD_DIR = os.path.join(FRONTEND_DIR, "static", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

@app.post("/admin/tutorials/upload-image")
async def upload_tutorial_image(file: UploadFile = File(...)):
    ext = file.filename.split(".")[-1].lower()
    if ext not in ["jpg", "jpeg", "png", "webp"]:
        raise HTTPException(status_code=400, detail="Invalid image type")

    filename = f"{uuid.uuid4()}.{ext}"
    file_path = os.path.join(UPLOAD_DIR, filename)

    with open(file_path, "wb") as f:
        f.write(await file.read())

    return {
        "image_url": f"/static/uploads/{filename}"
    }

# ================== GEMINI AI CHATBOT ==========================================
@app.post("/api/chat")
async def chat_with_gemini(payload: dict = Body(...)):
    user_message = payload.get("message", "")
    
    if not user_message:
        raise HTTPException(status_code=400, detail="Message is required")
    
    # System prompt for ethical hacking context
    system_prompt = """You are an AI assistant for an Ethical Hacking Learning Platform. 
    You help users learn about cybersecurity, ethical hacking techniques, CTF challenges, 
    penetration testing, and security best practices. Keep responses concise, helpful, 
    and educational. Always emphasize ethical and legal use of security knowledge."""
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    
    request_body = {
        "contents": [
            {
                "parts": [
                    {"text": f"{system_prompt}\n\nUser: {user_message}"}
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 1024
        }
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=request_body)
            
            if response.status_code != 200:
                error_data = response.json()
                error_msg = error_data.get("error", {}).get("message", "Unknown error")
                if response.status_code == 429:
                    raise HTTPException(status_code=429, detail="API quota exceeded. Please try again later or check your billing.")
                elif response.status_code == 400:
                    raise HTTPException(status_code=400, detail=f"Bad request: {error_msg}")
                else:
                    raise HTTPException(status_code=response.status_code, detail=f"Gemini API error: {error_msg}")
            
            data = response.json()
            
            # Extract the response text
            ai_response = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "Sorry, I couldn't generate a response.")
            
            return {"response": ai_response}
    
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Request to Gemini API timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ================== PROGRESS ANALYTICS API ==========================================

@app.get("/api/user/progress-stats")
def get_progress_stats(email: str):
    """Get detailed progress statistics including success rate, streaks, etc."""
    user = get_user_by_email(email)
    if not user:
        return JSONResponse({"error": "User not found"}, status_code=404)
    
    user_id, name, user_email, level, xp, total_challenged, rank = user
    
    conn = get_db()
    cur = conn.cursor()
    
    # Get total challenges available
    cur.execute("SELECT COUNT(*) FROM challenges WHERE is_published = 1")
    total_available = cur.fetchone()[0]
    
    # Get challenges solved
    cur.execute("""
        SELECT COUNT(*) FROM user_challenges 
        WHERE user_id = ?
    """, (user_id,))
    solved = cur.fetchone()[0]
    
    # Calculate success rate
    success_rate = (solved / total_available * 100) if total_available > 0 else 0
    
    # Get current streak (consecutive days with challenge completion)
    cur.execute("""
        SELECT DATE(solved_at) as completion_date 
        FROM user_challenges 
        WHERE user_id = ? 
        ORDER BY DATE(solved_at) DESC 
        LIMIT 365
    """, (user_id,))
    
    completion_dates = [row[0] for row in cur.fetchall()]
    current_streak = 0
    best_streak = 0
    streak_counter = 0
    
    if completion_dates:
        from datetime import datetime, timedelta
        today = datetime.now().date()
        for i, date_str in enumerate(completion_dates):
            date = datetime.strptime(date_str, "%Y-%m-%d").date()
            if i == 0:
                if (today - date).days <= 1:
                    current_streak = 1
                    streak_counter = 1
            else:
                prev_date = datetime.strptime(completion_dates[i-1], "%Y-%m-%d").date()
                if (prev_date - date).days == 1:
                    streak_counter += 1
                    if (today - date).days <= streak_counter:
                        current_streak = streak_counter
                else:
                    best_streak = max(best_streak, streak_counter)
                    streak_counter = 1
        best_streak = max(best_streak, streak_counter)
    
    # Get challenges by category
    cur.execute("""
        SELECT c.category, COUNT(uc.id) as completed, COUNT(DISTINCT c.id) as total
        FROM challenges c
        LEFT JOIN user_challenges uc ON c.id = uc.challenge_id AND uc.user_id = ?
        WHERE c.is_published = 1
        GROUP BY c.category
    """, (user_id,))
    
    categories = {}
    for category, completed, total in cur.fetchall():
        if category:
            categories[category] = {
                "completed": completed if completed else 0,
                "total": total,
                "percentage": (completed / total * 100) if total > 0 else 0
            }
    
    # Get challenges by difficulty
    cur.execute("""
        SELECT c.difficulty, COUNT(uc.id) as completed, COUNT(DISTINCT c.id) as total
        FROM challenges c
        LEFT JOIN user_challenges uc ON c.id = uc.challenge_id AND uc.user_id = ?
        WHERE c.is_published = 1
        GROUP BY c.difficulty
    """, (user_id,))
    
    difficulty_stats = {}
    for difficulty, completed, total in cur.fetchall():
        if difficulty:
            difficulty_stats[difficulty] = {
                "completed": completed if completed else 0,
                "total": total,
                "percentage": (completed / total * 100) if total > 0 else 0
            }
    
    conn.close()
    
    return JSONResponse({
        "username": name,
        "level": level,
        "xp": xp,
        "rank": rank,
        "total_challenges": solved,
        "total_available": total_available,
        "success_rate": round(success_rate, 2),
        "current_streak": current_streak,
        "best_streak": best_streak,
        "categories": categories,
        "difficulty_stats": difficulty_stats
    })


@app.get("/api/user/challenge-history")
def get_challenge_history(email: str, days: int = 30):
    """Get challenge completion history for the past N days"""
    user = get_user_by_email(email)
    if not user:
        return JSONResponse({"error": "User not found"}, status_code=404)
    
    user_id = user[0]
    
    from datetime import datetime, timedelta
    start_date = datetime.now() - timedelta(days=days)
    
    conn = get_db()
    cur = conn.cursor()
    
    # Get daily challenge counts
    cur.execute("""
        SELECT 
            DATE(solved_at) as date,
            COUNT(*) as count,
            SUM(c.points) as points
        FROM user_challenges uc
        JOIN challenges c ON uc.challenge_id = c.id
        WHERE uc.user_id = ? AND uc.solved_at >= ?
        GROUP BY DATE(solved_at)
        ORDER BY date ASC
    """, (user_id, start_date.isoformat()))
    
    history = {}
    for date, count, points in cur.fetchall():
        history[date] = {
            "challenges": count,
            "points": points if points else 0
        }
    
    # Fill in missing dates with 0
    current_date = start_date.date()
    today = datetime.now().date()
    all_dates = {}
    
    while current_date <= today:
        date_str = current_date.isoformat()
        all_dates[date_str] = history.get(date_str, {"challenges": 0, "points": 0})
        current_date += timedelta(days=1)
    
    conn.close()
    
    return JSONResponse({
        "history": all_dates,
        "days": days
    })


@app.get("/api/user/performance-metrics")
def get_performance_metrics(email: str):
    """Get user performance metrics including category breakdown, difficulty performance"""
    user = get_user_by_email(email)
    if not user:
        return JSONResponse({"error": "User not found"}, status_code=404)
    
    user_id = user[0]
    
    conn = get_db()
    cur = conn.cursor()
    
    # Average solve time per category (based on when challenges were solved)
    cur.execute("""
        SELECT 
            c.category,
            AVG(CAST((julianday(uc.solved_at) - julianday(c.created_at)) * 24 * 60 AS FLOAT)) as avg_minutes,
            COUNT(uc.id) as total_solved
        FROM user_challenges uc
        JOIN challenges c ON uc.challenge_id = c.id
        WHERE uc.user_id = ?
        GROUP BY c.category
    """, (user_id,))
    
    solve_times = {}
    for category, avg_minutes, total_solved in cur.fetchall():
        if category:
            solve_times[category] = {
                "avg_minutes": round(avg_minutes, 2) if avg_minutes else 0,
                "total_solved": total_solved
            }
    
    # Performance vs average (percentage)
    cur.execute("""
        SELECT COUNT(*) FROM user_challenges WHERE user_id = ?
    """, (user_id,))
    user_solved = cur.fetchone()[0]
    
    cur.execute("""
        SELECT AVG(challenge_count) FROM (
            SELECT COUNT(*) as challenge_count
            FROM user_challenges
            GROUP BY user_id
        )
    """)
    global_avg = cur.fetchone()[0] or 0
    
    performance_vs_avg = ((user_solved - global_avg) / global_avg * 100) if global_avg > 0 else 0
    
    # Get challenges by difficulty performance
    cur.execute("""
        SELECT 
            c.difficulty,
            COUNT(uc.id) as completed,
            AVG(c.points) as avg_points
        FROM user_challenges uc
        JOIN challenges c ON uc.challenge_id = c.id
        WHERE uc.user_id = ?
        GROUP BY c.difficulty
    """, (user_id,))
    
    difficulty_performance = {}
    for difficulty, completed, avg_points in cur.fetchall():
        if difficulty:
            difficulty_performance[difficulty] = {
                "completed": completed,
                "avg_points": round(avg_points, 2) if avg_points else 0
            }
    
    conn.close()
    
    return JSONResponse({
        "solve_times": solve_times,
        "performance_vs_avg": round(performance_vs_avg, 2),
        "difficulty_performance": difficulty_performance,
        "total_challenges_solved": user_solved
    })


@app.get("/api/user/streak-info")
def get_streak_info(email: str):
    """Get detailed streak information"""
    user = get_user_by_email(email)
    if not user:
        return JSONResponse({"error": "User not found"}, status_code=404)
    
    user_id = user[0]
    
    conn = get_db()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT DATE(solved_at) as completion_date
        FROM user_challenges
        WHERE user_id = ?
        ORDER BY DATE(solved_at) DESC
    """, (user_id,))
    
    from datetime import datetime, timedelta
    completion_dates = [row[0] for row in cur.fetchall()]
    
    current_streak = 0
    best_streak = 0
    streak_counter = 0
    current_streak_start = None
    best_streak_start = None
    best_streak_end = None
    
    if completion_dates:
        today = datetime.now().date()
        
        for i, date_str in enumerate(completion_dates):
            date = datetime.strptime(date_str, "%Y-%m-%d").date()
            
            if i == 0:
                if (today - date).days <= 1:
                    current_streak = 1
                    streak_counter = 1
                    current_streak_start = date
            else:
                prev_date = datetime.strptime(completion_dates[i-1], "%Y-%m-%d").date()
                if (prev_date - date).days == 1:
                    streak_counter += 1
                    if (today - date).days <= streak_counter:
                        current_streak = streak_counter
                        current_streak_start = date
                else:
                    if streak_counter > best_streak:
                        best_streak = streak_counter
                        best_streak_end = prev_date
                        best_streak_start = date
                    streak_counter = 1
        
        if streak_counter > best_streak:
            best_streak = streak_counter
            best_streak_end = completion_dates[-1]
            best_streak_start = datetime.strptime(completion_dates[-1], "%Y-%m-%d").date()
    
    conn.close()
    
    return JSONResponse({
        "current_streak": current_streak,
        "current_streak_start": current_streak_start.isoformat() if current_streak_start else None,
        "best_streak": best_streak,
        "best_streak_start": best_streak_start.isoformat() if best_streak_start else None,
        "best_streak_end": best_streak_end.isoformat() if best_streak_end else None
    })


@app.get("/api/user/milestones")
def get_milestones(email: str):
    """Get user milestones and achievements"""
    user = get_user_by_email(email)
    if not user:
        return JSONResponse({"error": "User not found"}, status_code=404)
    
    user_id, name, user_email, level, xp, total_challenges, rank = user
    
    conn = get_db()
    cur = conn.cursor()
    
    # Get next milestones
    cur.execute("SELECT COUNT(*) FROM challenges WHERE is_published = 1")
    total_available = cur.fetchone()[0]
    
    milestones = []
    milestone_thresholds = [5, 10, 25, 50, 100]
    
    for threshold in milestone_thresholds:
        completed = total_challenges >= threshold
        progress = min(total_challenges, threshold)
        next_goal = f"Complete {threshold} challenges"
        
        milestones.append({
            "threshold": threshold,
            "completed": completed,
            "progress": progress,
            "goal": next_goal
        })
    
    # Rank milestones
    rank_milestones = [
        {"rank": 1, "label": "Top 50%", "milestone": rank <= (10000 * 0.5)},
        {"rank": 10, "label": "Top 10%", "milestone": rank <= (10000 * 0.1)},
        {"rank": 100, "label": "Top 1%", "milestone": rank <= (10000 * 0.01)}
    ]
    
    conn.close()
    
    return JSONResponse({
        "challenge_milestones": milestones,
        "rank_milestones": rank_milestones,
        "current_level": level,
        "next_level_xp": (level * 500)  # Assuming 500 XP per level
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
