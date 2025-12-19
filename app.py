from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from openai import OpenAI
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'default_secret_key')

# Database setup
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///tasks.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Task model (same as before)
class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(500), nullable=False)
    recipient_email = db.Column(db.String(120))
    due_date = db.Column(db.String(50))
    status = db.Column(db.String(50), default='pending')

# OpenAI client
client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

def parse_reminder(reminder):
    """Use AI to parse reminder into task components."""
    prompt = f"""
    Parse this reminder into a task:
    Reminder: {reminder}
    Extract:
    - Description: The main task action and details.
    - Recipient Email: If mentioned (e.g., 'email john@example.com'), else none.
    - Due Date: Absolute (YYYY-MM-DD) or relative (convert to absolute based on today {datetime.today().date()}). If ambiguous like 'soon', return 'ambiguous'.
    Respond in JSON: {{"description": "...", "recipient_email": "...", "due_date": "YYYY-MM-DD or ambiguous"}}
    """
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=150
    )
    try:
        parsed = eval(response.choices[0].message.content.strip())
        if parsed['due_date'] == 'ambiguous':
            raise ValueError("Ambiguous due date")
        # Handle relative dates if needed (backup)
        if 'next week' in reminder.lower():
            parsed['due_date'] = (datetime.today() + timedelta(days=7)).strftime('%Y-%m-%d')
        return parsed
    except Exception as e:
        flash(f"Error parsing: {str(e)}. Please clarify.")
        return None

@app.route('/')
def home():
    tasks = Task.query.all()
    return render_template('index.html', message='Welcome to AI To-Do Email Agent!', tasks=tasks)

@app.route('/add_task', methods=['POST'])
def add_task():
    # Manual add (for testing)
    description = request.form.get('description')
    if description:
        recipient_email = request.form.get('recipient_email')
        due_date = request.form.get('due_date')
        new_task = Task(description=description, recipient_email=recipient_email, due_date=due_date)
        db.session.add(new_task)
        db.session.commit()
        flash("Task added manually.")
    return redirect(url_for('home'))

@app.route('/add_reminder', methods=['POST'])
def add_reminder():
    reminder = request.form.get('reminder')
    if reminder:
        parsed = parse_reminder(reminder)
        if parsed:
            new_task = Task(description=parsed['description'], recipient_email=parsed.get('recipient_email'), due_date=parsed['due_date'])
            db.session.add(new_task)
            db.session.commit()
            flash("Reminder parsed and task added.")
        else:
            flash("Could not parse reminder. Try again with more details.")
    return redirect(url_for('home'))

# Create DB
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True)