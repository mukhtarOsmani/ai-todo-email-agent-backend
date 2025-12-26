from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
import os
import openai
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
import base64
from email.mime.text import MIMEText
from apscheduler.schedulers.background import BackgroundScheduler
import datetime
import logging
import atexit
from models import Task
from extensions import db
load_dotenv()

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = os.getenv('SECRET_KEY')

CORS(app)
client = openai.OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
db.init_app(app)

with app.app_context():
    db.create_all()


@app.route('/')
def home():
    return "AI To-Do Email Agent Backend is running!"


@app.route('/tasks', methods=['POST'])
def create_task():
    data = request.json
    if not data or 'description' not in data or 'recipient_email' not in data or 'due_date' not in data:
        return jsonify({'error': 'Missing fields'}), 400

    task = Task(
        description=data['description'],
        recipient_email=data['recipient_email'],
        due_date=data['due_date']
    )
    db.session.add(task)
    db.session.commit()
    return jsonify({'message': 'Task created', 'task_id': task.id}), 201


@app.route('/tasks', methods=['GET'])
def get_tasks():
    tasks = Task.query.all()
    return jsonify([{
        'id': t.id,
        'description': t.description,
        'recipient_email': t.recipient_email,
        'due_datetime': t.due_datetime.isoformat() if t.due_datetime else None,
        'status': t.status
    } for t in tasks])


def parse_reminder(reminder):
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a precise task parser. Extract exactly three fields from the user input:\n"
                    "1. Task description (what needs to be done)\n"
                    "2. Recipient email (must be a valid email like name@example.com; if no email is mentioned or unclear, output 'None')\n"
                    "3. Due date (in YYYY-MM-DD format if possible, or relative like 'next Friday', or 'None' if not mentioned)\n\n"
                    "Output ONLY in this exact format, no extra text or explanations:\n"
                    "description|email|due_date\n\n"
                    "Examples:\n"
                    "Input: Remind john.doe@gmail.com about the report tomorrow\n"
                    "Output: Write and send the report|john.doe@gmail.com|tomorrow\n\n"
                    "Input: Call mom about dinner\n"
                    "Output: Call mom about dinner|None|None"
                )
            },
            {
                "role": "user",
                "content": reminder
            }
        ]
    )
    extracted = response.choices[0].message.content.strip()
    parts = extracted.split('|')
    if len(parts) != 3:
        raise ValueError("Invalid parse format from AI")
    return {
        'description': parts[0].strip(),
        'recipient_email': parts[1].strip(),
        'due_date': parts[2].strip()
    }


@app.route('/reminders', methods=['POST'])
def add_reminder():
    data = request.json
    if not data or 'reminder' not in data:
        return jsonify({'error': 'Missing reminder'}), 400

    try:
        text_to_parse = data['reminder']
        parsed = parse_reminder(text_to_parse)

        # Use parsed description and email
        description = parsed['description']
        recipient_email = parsed['recipient_email']
        if recipient_email == 'None' or '@' not in recipient_email:
            return jsonify({'error': 'Could not extract a valid email address from the reminder'}), 400
        due_input = data.get('due_datetime') or parsed['due_date']

        if due_input and due_input != 'None':
            from dateutil import parser
            due_datetime = parser.parse(due_input)
        else:
            due_datetime = None

        task = Task(
            description=description,
            recipient_email=recipient_email,
            due_datetime=due_datetime
        )
        db.session.add(task)
        db.session.commit()

        return jsonify({
            'message': 'Task created from reminder',
            'task_id': task.id,
            'parsed': {
                'description': description,
                'email': recipient_email,
                'due_datetime': due_datetime.isoformat() if due_datetime else None
            }
        }), 201

    except Exception as e:
        logger.error(f"Error creating task: {str(e)}")
        return jsonify({'error': str(e)}), 500

def generate_email(task):
    if task.due_datetime:
        due_str = task.due_datetime.strftime('%B %d, %Y at %I:%M %p')
    else:
        due_str = 'No specific due date'

    prompt = (
        f"Generate a professional email reminding about a task. "
        f"Task description: {task.description}. "
        f"Recipient: {task.recipient_email}. "
        f"Due: {due_str}."
    )

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "system",
                "content": "You are an email drafter. Output format exactly: Subject: [subject]\nBody: [body]"
            },
            {
                "role": "user",
                "content": prompt
            }
        ]
    )

    content = response.choices[0].message.content.strip()
    parts = content.split('\nBody: ')
    if len(parts) != 2:
        raise ValueError("Invalid email format from AI")

    subject = parts[0].replace('Subject: ', '').strip()
    body = parts[1].strip()

    return {'subject': subject, 'body': body}

@app.route('/tasks/<int:task_id>/generate_email', methods=['GET'])
def generate_email_for_task(task_id):
    task = Task.query.get_or_404(task_id)
    try:
        email = generate_email(task)
        return jsonify({'email': email})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def get_gmail_service():
    creds = None
    scope = os.getenv('GMAIL_SCOPE')
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', [scope])
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file('credentials.json', [scope])
        creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return build('gmail', 'v1', credentials=creds)


def send_email(service, to, subject, body, sender_email):
    to = to.strip()
    if not to or '@' not in to:
        raise ValueError("Invalid or empty recipient email")

    message = MIMEText(body)
    message['to'] = to
    message['from'] = sender_email
    message['subject'] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    try:
        service.users().messages().send(userId='me', body={'raw': raw}).execute()
        return True
    except HttpError as error:
        raise error

@app.route('/tasks/<int:task_id>/confirm_and_send', methods=['POST'])
def confirm_and_send(task_id):
    task = Task.query.get_or_404(task_id)
    if task.status != 'pending':
        return jsonify({'error': 'Task not pending'}), 400

    if not task.recipient_email or '@' not in task.recipient_email.strip():
        return jsonify({'error': 'Cannot send: Invalid or missing recipient email'}), 400

    data = request.json or {}
    email = (
        {'subject': data['subject'], 'body': data['body']}
        if data.get('subject') and data.get('body')
        else generate_email(task)
    )

    service = get_gmail_service()
    profile = service.users().getProfile(userId='me').execute()
    sender_email = profile['emailAddress']
    try:
        send_email(service, task.recipient_email, email['subject'], email['body'], sender_email)
        task.status = 'sent'
        db.session.commit()
        return jsonify({'message': 'Email sent'})
    except HttpError as e:
        logger.error(f"Gmail API error: {e.resp.status} {e.content}")
        return jsonify({'error': f"Gmail API error: {str(e)}"}), 500
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return jsonify({'error': str(e)}), 500


def check_due_tasks():
    with app.app_context():
        today_str = datetime.datetime.now().strftime('%Y-%m-%d')
        due_tasks = Task.query.filter(
            Task.due_datetime <= datetime.datetime.now(),
            Task.status == 'pending'
        ).all()

        for task in due_tasks:
            logger.info(f"Due task {task.id}: {task.description} → {task.recipient_email}")
            try:
                email = generate_email(task)
                logger.info(f"Generated draft for task {task.id} | Subject: {email['subject']}")
            except Exception as e:
                logger.error(f"Failed to generate email for task {task.id}: {str(e)}")


@app.route('/analytics', methods=['GET'])
def analytics():
    total = Task.query.count()
    sent = Task.query.filter_by(status='sent').count()
    return jsonify({'total_tasks': total, 'sent': sent})


scheduler = BackgroundScheduler()
scheduler.add_job(check_due_tasks, 'interval', minutes=1)
atexit.register(lambda: scheduler.shutdown())

if __name__ == '__main__':
    with app.app_context():
        scheduler.start()
        logger.info("Scheduler started – checking due tasks every minute")
    app.run(debug=True)