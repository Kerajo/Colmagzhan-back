import sqlite3
import os
import json
import time
from flask import Flask, request, jsonify, g, Response, send_from_directory
from flask_cors import CORS
from flask_apscheduler import APScheduler
from werkzeug.security import generate_password_hash, check_password_hash
from urllib.parse import quote

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)
scheduler = APScheduler()
scheduler.init_app(app)

DATABASE = 'account.db'
BASE_URL = "http://127.0.0.1:5000"
STORAGE_DIR = 'storage'

categories = []
storage_data = []
id_counter = 1
category_id_counter = 1

def find_category(data, category_id):
    """Recursively find category by idCategory."""
    if isinstance(data, dict):
        data = [data]
    for item in data:
        if 'idCategory' in item and item['idCategory'] == category_id:
            return item
        if 'children' in item:
            found = find_category(item['children'], category_id)
            if found:
                return found
    return None

def scan_directory(path, is_root=True, parent_category_id=None, relative_path=''):
    global id_counter, category_id_counter
    result = []
    for entry in os.scandir(path):
        entry_relative_path = os.path.join(relative_path, entry.name)
        if entry.is_dir():
            category_id = category_id_counter if is_root else parent_category_id
            item = {
                'id': id_counter,
                'name': entry.name,
                'type': 'Category' if is_root else 'folder',
                'idCategory': category_id
            }
            id_counter += 1
            if is_root:
                category_id_counter += 1
            item['children'] = scan_directory(os.path.join(path, entry.name), False, category_id, entry_relative_path)
            result.append(item)
        elif entry.is_file():
            stats = entry.stat()
            creation_date = time.strftime('%d.%m.%Y', time.localtime(stats.st_ctime))
            size = os.path.getsize(entry.path)
            size_label = 'KB' if size < 1024 * 1024 else 'MB'
            size = size / 1024 if size_label == 'KB' else size / (1024 * 1024)
            file_name, file_extension = os.path.splitext(entry.name)
            file_format = file_extension[1:] if file_extension else 'unknown'
            file_url = f"{BASE_URL}/files/storage/{quote(entry_relative_path.replace(os.sep, '/'))}"
            item = {
                'id': id_counter,
                'name': file_name,
                'type': 'file',
                'fileDate': creation_date,
                'fileType': file_format,
                'fileSize': f"{size:.2f} {size_label}",
                'filepath': file_url,
                'categoryId': parent_category_id
            }
            id_counter += 1
            result.append(item)
    return result


def update_categories():
    """Scan the directory and update the list of categories."""
    global categories
    try:
        folder_names = [name for name in os.listdir(STORAGE_DIR) if os.path.isdir(os.path.join(STORAGE_DIR, name))]
        categories = [{"idCategory": idx + 1, "textCategory": name} for idx, name in enumerate(folder_names)]
        print("Categories updated:", categories)
    except Exception as e:
        print("Error updating categories:", e)

def update_storage_data():
    """Scan the directory and update the storage data."""
    global storage_data, id_counter, category_id_counter
    id_counter, category_id_counter = 1, 1
    try:
        storage_data = scan_directory(STORAGE_DIR)
        print("Storage data updated")
    except Exception as e:
        print("Error updating storage data:", e)

def get_db():
    if not hasattr(g, '_database'):
        g._database = sqlite3.connect(DATABASE)
        g._database.row_factory = sqlite3.Row
    return g._database

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

@app.route('/reg', methods=['POST'])
def register():
    data = request.get_json()
    required_fields = ['login', 'password', 'name', 'surname']
    if not all(field in data for field in required_fields):
        return jsonify({"msg": "Missing required fields"}), 400

    login, password, name, surname = data['login'], data['password'], data['name'], data['surname']
    post = data.get('post')
    token = generate_password_hash(login + password + name + surname)
    hashed_password = generate_password_hash(password)

    db = get_db()
    try:
        db.execute(
            'INSERT INTO users (login, password, name, surname, post, token) VALUES (?, ?, ?, ?, ?, ?)',
            (login, hashed_password, name, surname, post, token)
        )
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"msg": "User with this login already exists"}), 409

    return jsonify({"msg": "User registered successfully", "token": token}), 201

@app.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    login, password = data['login'], data['password']

    db = get_db()
    user = db.execute('SELECT * FROM users WHERE login = ?', (login,)).fetchone()

    if user and check_password_hash(user['password'], password):
        return jsonify(
            id=user['id'], login=user['login'], name=user['name'], surname=user['surname'], post=user['post'], token=user['token']
        ), 200

    return jsonify({"msg": "Bad username or password"}), 401

@app.route('/categories', methods=['GET'])
def get_categories():
    return jsonify(categories)

@app.route('/storage', methods=['GET'])
def get_storage():
    category_id = request.args.get('idCategory', type=int)
    data = storage_data
    if category_id is not None:
        data = find_category(storage_data, category_id)
        if data is None:
            data = {'error': 'Category not found'}
    return jsonify(data)

@app.route('/files/storage/<path:filename>', methods=['GET'])
def download_file(filename):
    return send_from_directory(STORAGE_DIR, filename)

def init_db():
    with app.app_context():
        db = get_db()
        db.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            login TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            name TEXT NOT NULL,
            surname TEXT NOT NULL DEFAULT '',
            post TEXT,
            token TEXT NOT NULL
        );
        ''')
        db.commit()

if __name__ == '__main__':
    update_categories()
    update_storage_data()
    scheduler.add_job(id='Update Categories', func=update_categories, trigger='interval', hours=1)
    scheduler.add_job(id='Update Storage Data', func=update_storage_data, trigger='interval', hours=1)
    scheduler.start()
    init_db()
    app.run(debug=True)
