from extensions import db  # Import the global db

class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(500), nullable=False)
    recipient_email = db.Column(db.String(120), nullable=False)
    due_datetime = db.Column(db.DateTime)
    status = db.Column(db.String(50), default='pending')  # pending, confirmed, sent