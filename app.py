from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'default_secret_key')

# Database setup
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///tasks.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Task model
class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(500), nullable=False)
    recipient_email = db.Column(db.String(120))
    due_date = db.Column(db.String(50))
    status = db.Column(db.String(50), default='pending')

@app.route('/')
def home():
    tasks = Task.query.all()  # Fetch all tasks
    return render_template('index.html', message='Welcome to AI To-Do Email Agent!', tasks=tasks)

@app.route('/add_task', methods=['POST'])
def add_task():
    description = request.form.get('description')
    recipient_email = request.form.get('recipient_email')
    due_date = request.form.get('due_date')
    if description:
        new_task = Task(description=description, recipient_email=recipient_email, due_date=due_date)
        db.session.add(new_task)
        db.session.commit()
    return redirect(url_for('home'))

# Create DB if not exists
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True)