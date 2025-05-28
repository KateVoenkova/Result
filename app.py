from collections import defaultdict
from sqlalchemy import func
from flask import render_template, request, redirect, url_for, flash, current_app
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, make_response
from flask_bootstrap import Bootstrap
from flask_migrate import Migrate
from flask_login import LoginManager, login_user, logout_user, current_user, login_required
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User, Book, Character, CharacterRelationship, BookAnalysis, ReadingProgress
from name_parser import get_names_from_file
from relationships import find_relationships
import os
import uuid
import chardet
import logging
from functools import wraps
from flask import send_from_directory, abort
from sqlalchemy import or_
from datetime import datetime
from flask import request

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-123'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///library.db'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['ALLOWED_EXTENSIONS'] = {'txt'}
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Создаем директорию для базы данных, если ее нет
if not os.path.exists('/app/data'):
    os.makedirs('/app/data', exist_ok=True)

# Инициализация расширений
db.init_app(app)
Bootstrap(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
migrate = Migrate(app, db)

# Настройка логирования
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            flash('Доступ запрещен', 'danger')
            return redirect(url_for('library'))
        return f(*args, **kwargs)

    return decorated


# ---------------------------
# Маршруты аутентификации
# ---------------------------

@app.route('/')
def home():
    return render_template('home.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()

        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            flash('Успешный вход!', 'success')
            return redirect(url_for('dashboard'))
        flash('Неверные учетные данные', 'danger')
    return render_template('auth/login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = generate_password_hash(request.form['password'])

        if User.query.filter_by(username=username).first():
            flash('Имя пользователя уже занято', 'danger')
            return redirect(url_for('register'))

        new_user = User(username=username, password_hash=password)
        db.session.add(new_user)
        db.session.commit()
        login_user(new_user)
        flash('Аккаунт успешно создан!', 'success')
        return redirect(url_for('dashboard'))
    return render_template('auth/register.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Вы вышли из системы', 'info')
    return redirect(url_for('home'))


# ---------------------------
# Личный кабинет и профили
# ---------------------------

@app.route('/dashboard')
@login_required
def dashboard():
    # Получаем только неудаленные книги и анализы
    books = Book.query.filter_by(user_id=current_user.id, is_deleted=False).all()
    analyses = BookAnalysis.query.filter_by(user_id=current_user.id, is_deleted=False).all()

    # Получаем архивные данные
    archived_books = Book.query.filter_by(user_id=current_user.id, is_deleted=True).all()
    archived_analyses = BookAnalysis.query.filter_by(user_id=current_user.id, is_deleted=True).all()

    return render_template('dashboard.html',
                           books=books,
                           analyses=analyses,
                           archived_books=archived_books,
                           archived_analyses=archived_analyses)


@app.route('/user/<username>')
@login_required
def user_profile(username):
    user = User.query.filter_by(username=username).first_or_404()
    books = Book.query.filter_by(user_id=user.id, is_deleted=False).all()

    return render_template('user/profile.html',
                           user=user,
                           books=books,
                           title=f"Профиль {username}")


# ---------------------------
# Работа с книгами
# ---------------------------

@app.route('/library')
def library():
    # Получаем только НЕархивные книги (is_deleted=False)
    books = Book.query.filter_by(is_deleted=False).all()
    return render_template('books/library.html', books=books)

@app.errorhandler(413)
def request_entity_too_large(error):
    flash('Файл слишком большой. Максимальный размер - 50 МБ', 'danger')
    return redirect(url_for('upload'))


@app.route('/upload', methods=['GET', 'POST'])
@login_required
def upload():
    if request.method == 'POST':
        success_count = 0
        error_messages = []

        # Обрабатываем каждую книгу в форме
        for i, file in enumerate(request.files.getlist('files')):
            try:
                # Пропускаем пустые файлы
                if file.filename == '':
                    continue

                # Проверка размера файла (максимум 50 МБ)
                if file.content_length > 50 * 1024 * 1024:
                    error_messages.append(f"Файл '{file.filename}' слишком большой (максимум 50 МБ)")
                    continue

                # Получаем данные для текущей книги
                title = request.form.get(f'titles[{i}]', '').strip()
                author_last_name = request.form.get(f'author_last_names[{i}]', '').strip()
                author_first_name = request.form.get(f'author_first_names[{i}]', '').strip()
                author_middle_name = request.form.get(f'author_middle_names[{i}]', '').strip()
                description = request.form.get(f'descriptions[{i}]', '').strip()

                # Валидация данных
                if not all([title, author_last_name, author_first_name]):
                    error_messages.append(f"Не заполнены обязательные поля для файла '{file.filename}'")
                    continue

                if not file.filename.lower().endswith('.txt'):
                    error_messages.append(f"Файл '{file.filename}' не является текстовым (.txt)")
                    continue

                # Создаем папку uploads если её нет
                os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

                # Генерируем уникальное имя файла
                filename = f"{uuid.uuid4()}.txt"
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)

                # Сохраняем файл
                file.save(filepath)

                # Определяем кодировку файла
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        text = f.read()
                except UnicodeDecodeError:
                    with open(filepath, 'rb') as f:
                        encoding = chardet.detect(f.read())['encoding']
                    with open(filepath, 'r', encoding=encoding) as f:
                        text = f.read()

                # Создаем запись о книге в БД
                new_book = Book(
                    title=title,
                    user_id=current_user.id,
                    author_last_name=author_last_name,
                    author_first_name=author_first_name,
                    author_middle_name=author_middle_name if author_middle_name else None,
                    description=description,
                    original_file=filename
                )
                db.session.add(new_book)
                db.session.commit()

                # Извлекаем имена персонажей
                names = get_names_from_file(filepath)
                if not names:
                    error_messages.append(f"Не найдено имен персонажей в файле '{file.filename}'")

                # Добавляем персонажей в БД
                for name in names:
                    character = Character(
                        name=name,
                        normalized_name=name.lower().strip(),
                        description=f"Персонаж книги {title}",
                        book_id=new_book.id
                    )
                    db.session.add(character)

                db.session.commit()

                # Анализируем связи между персонажами
                find_relationships(new_book.id, filepath)

                success_count += 1

            except Exception as e:
                db.session.rollback()
                logger.error(f"Ошибка загрузки файла '{file.filename}': {str(e)}", exc_info=True)
                error_messages.append(f"Ошибка обработки файла '{file.filename}': {str(e)}")
                # Удаляем файл, если он был сохранен
                if 'filepath' in locals() and os.path.exists(filepath):
                    os.remove(filepath)

        # Формируем итоговое сообщение
        if success_count > 0:
            flash(f'Успешно загружено {success_count} книг', 'success')
        if error_messages:
            flash('Ошибки при загрузке: ' + '; '.join(error_messages), 'danger')

        return redirect(url_for('library'))

    return render_template('books/upload.html')


@app.route('/books/<int:book_id>', methods=['GET', 'POST'])
def book_page(book_id):
    book = Book.query.filter_by(id=book_id, is_deleted=False).first_or_404()
    edit_mode = request.args.get('edit', 'false').lower() == 'true'

    if request.method == 'POST':
        if book.user_id != current_user.id and not current_user.is_admin:
            flash('Доступ запрещен', 'danger')
            return redirect(url_for('library'))

        book.title = request.form['title']
        book.description = request.form['description']
        db.session.commit()
        flash('Изменения сохранены', 'success')
        return redirect(url_for('book_page', book_id=book.id))

    characters = Character.query.filter_by(book_id=book_id).all()
    return render_template('books/detail.html',
                           book=book,
                           characters=characters,
                           edit_mode=edit_mode)


@app.route('/books/<int:book_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_book(book_id):
    book = Book.query.get_or_404(book_id)
    if book.user_id != current_user.id and not current_user.is_admin:
        flash('Доступ запрещен', 'danger')
        return redirect(url_for('library'))

    if request.method == 'POST':
        book.title = request.form['title']
        book.description = request.form['description']
        db.session.commit()
        flash('Изменения сохранены', 'success')
        return redirect(url_for('book_page', book_id=book.id))

    return render_template('books/edit.html', book=book)


@app.route('/books/<int:book_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_book(book_id):
    book = Book.query.get_or_404(book_id)
    db.session.delete(book)
    db.session.commit()
    flash('Книга удалена', 'success')
    return redirect(url_for('library'))

# ---------------------------
# Авторы
# ---------------------------

@app.route('/authors')
def authors_index():
    try:
        # Получаем всех уникальных авторов с количеством книг
        authors_data = db.session.query(
            Book.author_last_name,
            Book.author_first_name,
            Book.author_middle_name,
            func.count(Book.id).label('book_count')
        ).filter(
            Book.author_last_name.isnot(None),
            Book.is_deleted == False
        ).group_by(
            Book.author_last_name,
            Book.author_first_name,
            Book.author_middle_name
        ).order_by(
            Book.author_last_name,
            Book.author_first_name,
            Book.author_middle_name
        ).all()

        # Группируем по первой букве фамилии
        authors_by_letter = defaultdict(list)
        for author in authors_data:
            if author.author_last_name:  # Проверка на случай пустого значения
                first_letter = author.author_last_name[0].upper()
                full_name = f"{author.author_last_name} {author.author_first_name}"
                if author.author_middle_name:
                    full_name += f" {author.author_middle_name}"

                authors_by_letter[first_letter].append({
                    'full_name': full_name,
                    'last_name': author.author_last_name,
                    'first_name': author.author_first_name,
                    'middle_name': author.author_middle_name,
                    'book_count': author.book_count
                })

        # Русский алфавит для указателя
        alphabet = [chr(i) for i in range(1040, 1072)]  # А-Я

        return render_template('books/authors_index.html',
                               authors_by_letter=authors_by_letter,
                               alphabet=alphabet,
                               title="Все авторы")
    except Exception as e:
        current_app.logger.error(f"Error in authors_index: {str(e)}")
        flash('Произошла ошибка при загрузке списка авторов', 'danger')
        return redirect(url_for('library'))


@app.route('/author')
def author_redirect():
    """Перенаправление для случая, если перешли без параметров"""
    return redirect(url_for('authors_index'))


@app.route('/author/<last_name>_<first_name>')
@app.route('/author/<last_name>_<first_name>_<middle_name>')
def author_detail(last_name, first_name, middle_name=None):
    try:
        # Находим книги автора
        query = Book.query.filter(
            Book.author_last_name == last_name,
            Book.author_first_name == first_name,
            Book.is_deleted == False
        )

        if middle_name and middle_name != 'None':
            query = query.filter(Book.author_middle_name == middle_name)
        else:
            query = query.filter(or_(
                Book.author_middle_name.is_(None),
                Book.author_middle_name == ''
            ))

        books = query.order_by(Book.title).all()

        # Формируем полное имя для заголовка
        full_name = f"{last_name} {first_name}"
        if middle_name and middle_name != 'None':
            full_name += f" {middle_name}"

        return render_template('books/author_detail.html',
                               author_name=full_name,
                               last_name=last_name,
                               first_name=first_name,
                               middle_name=middle_name,
                               books=books,
                               title=f"Автор: {full_name}")
    except Exception as e:
        current_app.logger.error(f"Error in author_detail: {str(e)}")
        flash('Произошла ошибка при загрузке страницы автора', 'danger')
        return redirect(url_for('authors_index'))


# Дополнительные маршруты для API (если нужно)
@app.route('/api/authors')
def api_authors_list():
    """JSON API для получения списка авторов"""
    authors = db.session.query(
        Book.author_name,
        func.count(Book.id).label('book_count')
    ).filter(
        Book.author_name.isnot(None)
    ).group_by(
        Book.author_name
    ).order_by(
        Book.author_name
    ).all()

    return jsonify([{
        'name': author.author_name,
        'book_count': author.book_count
    } for author in authors])

@app.route('/api/author/<author_name>/books')
def api_author_books(author_name):
    """JSON API для получения книг автора"""
    books = Book.query.filter(
        Book.author_name == author_name,
        Book.is_deleted == False
    ).order_by(Book.title).all()

    return jsonify([{
        'id': book.id,
        'title': book.title,
        'description': book.description,
        'uploader': book.author.username
    } for book in books])


# ---------------------------
# Работа с персонажами
# ---------------------------

@app.route('/books/<int:book_id>/characters')
@login_required
def manage_characters(book_id):
    book = Book.query.get_or_404(book_id)
    characters = Character.query.filter_by(book_id=book_id).all()
    return render_template('characters/manage.html', book=book, characters=characters)


@app.route('/characters/<int:character_id>')
@login_required
def character_details(character_id):
    character = Character.query.get_or_404(character_id)
    return render_template('characters/detail.html', character=character)


@app.route('/characters/<int:character_id>/edit', methods=['POST'])
@login_required
def edit_character(character_id):
    character = Character.query.get_or_404(character_id)
    character.name = request.form['name']
    character.description = request.form['description']
    db.session.commit()
    flash('Изменения сохранены', 'success')
    return redirect(url_for('character_details', character_id=character.id))

@app.route('/characters/<int:character_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_character(character_id):
    character = Character.query.get_or_404(character_id)
    db.session.delete(character)
    db.session.commit()
    flash('Персонаж удален', 'success')
    return redirect(url_for('manage_characters', book_id=character.book_id))


# ---------------------------
# Визуализация графа
# ---------------------------

@app.route('/graph/<int:book_id>')
@login_required
def show_graph(book_id):
    book = Book.query.get_or_404(book_id)
    return render_template('books/graph.html', book=book)


@app.route('/api/books/<int:book_id>/graph')
@login_required
def get_graph_data(book_id):
    characters = Character.query.filter_by(book_id=book_id).all()
    relationships = CharacterRelationship.query.filter_by(book_id=book_id).all()

    nodes = [{
        "id": char.id,
        "name": char.name,
        "description": char.description
    } for char in characters]

    links = [{
        "source": rel.character1_id,
        "target": rel.character2_id,
        "value": rel.weight
    } for rel in relationships]

    return jsonify({"nodes": nodes, "links": links})


# ---------------------------
# Работа с анализами книг
# ---------------------------

@app.route('/books/<int:book_id>/add_analysis', methods=['GET', 'POST'])
@login_required
def add_analysis(book_id):
    book = Book.query.get_or_404(book_id)

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        content = request.form.get('content', '').strip()

        if not title or not content:
            flash('Заполните все обязательные поля', 'danger')
            return redirect(url_for('add_analysis', book_id=book.id))

        # Обработка файла, если он загружен
        if 'file' in request.files and request.files['file'].filename:
            file = request.files['file']
            if file.filename.lower().endswith('.txt'):
                try:
                    content = file.read().decode('utf-8')
                except UnicodeDecodeError:
                    try:
                        content = file.read().decode('windows-1251')
                    except:
                        flash('Ошибка декодирования файла', 'danger')
                        return redirect(url_for('add_analysis', book_id=book.id))

        new_analysis = BookAnalysis(
            title=title,
            content=content,
            user_id=current_user.id,
            book_id=book.id
        )
        db.session.add(new_analysis)
        db.session.commit()

        flash('Анализ успешно добавлен', 'success')
        return redirect(url_for('book_page', book_id=book.id))

    return render_template('books/add_analysis.html', book=book)


@app.route('/books/<int:book_id>/analyses')
def view_analyses(book_id):
    book = Book.query.get_or_404(book_id)
    analyses = BookAnalysis.query.filter_by(book_id=book.id).order_by(BookAnalysis.created_at.desc()).all()
    return render_template('books/analyses.html', book=book, analyses=analyses)


@app.route('/analysis/<int:analysis_id>')
def view_analysis(analysis_id):
    analysis = BookAnalysis.query.get_or_404(analysis_id)
    return render_template('books/analysis_detail.html', analysis=analysis)


@app.route('/analysis/<int:analysis_id>/delete', methods=['POST']) #
@login_required
def delete_analysis(analysis_id):
    analysis = BookAnalysis.query.get_or_404(analysis_id)

    if analysis.user_id != current_user.id and not current_user.is_admin:
        flash('Доступ запрещен', 'danger')
        return redirect(url_for('view_analyses', book_id=analysis.book_id))

    db.session.delete(analysis)
    db.session.commit()

    flash('Анализ удален', 'success')
    return redirect(url_for('view_analyses', book_id=analysis.book_id))

#---------------------------
# Личный кабинет и его тайны
#---------------------------

# Архивация и восстановление книг
@app.route('/books/<int:book_id>/archive', methods=['POST'])
@login_required
def archive_book(book_id):
    book = Book.query.get_or_404(book_id)
    if book.user_id != current_user.id and not current_user.is_admin:
        flash('Доступ запрещен', 'danger')
        return redirect(url_for('dashboard'))

    book.is_deleted = True  # Помечаем как архивную
    db.session.commit()
    flash('Книга перемещена в архив', 'success')
    return redirect(url_for('dashboard'))


@app.route('/books/<int:book_id>/restore', methods=['POST'])
@login_required
def restore_book(book_id):
    book = Book.query.get_or_404(book_id)
    if book.user_id != current_user.id and not current_user.is_admin:
        flash('Доступ запрещен', 'danger')
        return redirect(url_for('dashboard'))

    book.is_deleted = False
    db.session.commit()
    flash('Книга восстановлена из архива', 'success')
    return redirect(url_for('dashboard'))


# Архивация и восстановление анализов
@app.route('/analyses/<int:analysis_id>/archive', methods=['POST'])
@login_required
def archive_analysis(analysis_id):
    analysis = BookAnalysis.query.get_or_404(analysis_id)
    if analysis.user_id != current_user.id and not current_user.is_admin:
        flash('Доступ запрещен', 'danger')
        return redirect(url_for('dashboard'))

    analysis.is_deleted = True
    db.session.commit()
    flash('Анализ перемещен в архив', 'success')
    return redirect(url_for('dashboard'))


@app.route('/analyses/<int:analysis_id>/restore', methods=['POST'])
@login_required
def restore_analysis(analysis_id):
    analysis = BookAnalysis.query.get_or_404(analysis_id)
    if analysis.user_id != current_user.id and not current_user.is_admin:
        flash('Доступ запрещен', 'danger')
        return redirect(url_for('dashboard'))

    analysis.is_deleted = False
    db.session.commit()
    flash('Анализ восстановлен из архива', 'success')
    return redirect(url_for('dashboard'))


# ---------------------------
# Поиск
# ---------------------------

@app.route('/search')
def search():
    query = request.args.get('q', '').strip()
    filter_type = request.args.get('filter', 'title')
    year_from = request.args.get('year_from', type=int)
    year_to = request.args.get('year_to', type=int)
    sort = request.args.get('sort', 'relevance')

    if not query:
        return redirect(url_for('library'))

    # Базовый запрос
    q = Book.query.filter_by(is_deleted=False)

    # Фильтрация по типу
    if filter_type == 'title':
        q = q.filter(Book.title.ilike(f'%{query}%'))
    elif filter_type == 'author':
        q = q.filter(or_(
            Book.author_last_name.ilike(f'%{query}%'),
            Book.author_first_name.ilike(f'%{query}%'),
            Book.author_middle_name.ilike(f'%{query}%')
        ))
    elif filter_type == 'character':
        q = q.join(Character).filter(Character.name.ilike(f'%{query}%'))

    # Фильтрация по году
    if year_from:
        q = q.filter(Book.year >= year_from)
    if year_to:
        q = q.filter(Book.year <= year_to)

    # Сортировка
    if sort == 'title_asc':
        q = q.order_by(Book.title.asc())
    elif sort == 'title_desc':
        q = q.order_by(Book.title.desc())
    elif sort == 'year_asc':
        q = q.order_by(Book.year.asc())
    elif sort == 'year_desc':
        q = q.order_by(Book.year.desc())
    else:  # relevance
        q = q.order_by(Book.title.ilike(f'%{query}%').desc())

    results = q.all()

    return render_template('search_results.html',
                         query=query,
                         filter=filter_type,
                         results=results)


@app.route('/advanced_search', methods=['GET'])
def advanced_search():
    query = request.args.get('q', '').strip()
    filter_type = request.args.get('filter', 'title')
    year_from = request.args.get('year_from', type=int)
    year_to = request.args.get('year_to', type=int)
    sort = request.args.get('sort', 'relevance')

    sort_labels = {
        'relevance': 'По релевантности',
        'title_asc': 'По названию (А-Я)',
        'title_desc': 'По названию (Я-А)',
        'year_asc': 'По году (старые)',
        'year_desc': 'По году (новые)'
    }

    results = []
    if query:
        q = Book.query.filter_by(is_deleted=False)

        if filter_type == 'title':
            q = q.filter(Book.title.ilike(f'%{query}%'))
        elif filter_type == 'author':
            q = q.filter(or_(
                Book.author_last_name.ilike(f'%{query}%'),
                Book.author_first_name.ilike(f'%{query}%'),
                Book.author_middle_name.ilike(f'%{query}%')
            ))
        elif filter_type == 'character':
            q = q.join(Character).filter(Character.name.ilike(f'%{query}%'))

        if year_from:
            q = q.filter(Book.year >= year_from)
        if year_to:
            q = q.filter(Book.year <= year_to)

        if sort == 'title_asc':
            q = q.order_by(Book.title.asc())
        elif sort == 'title_desc':
            q = q.order_by(Book.title.desc())
        elif sort == 'year_asc':
            q = q.order_by(Book.year.asc())
        elif sort == 'year_desc':
            q = q.order_by(Book.year.desc())
        else:
            q = q.order_by(Book.title.ilike(f'{query}%').desc())

        results = q.all()

    return render_template('advanced_search.html',
                           query=query,
                           filter=filter_type,
                           year_from=year_from,
                           year_to=year_to,
                           sort=sort,
                           sort_labels=sort_labels,
                           results=results,
                           current_year=datetime.now().year)

# ---------------------------
# Чтение
# ---------------------------

from flask import send_from_directory
import os
import chardet
from datetime import datetime


# ... другие импорты ...

@app.route('/book/<int:book_id>/read')
@login_required
def read_book(book_id):
    """Основной route для чтения книги с разбивкой на страницы"""
    book = Book.query.get_or_404(book_id)

    # Проверка прав доступа
    if book.is_deleted and book.user_id != current_user.id and not current_user.is_admin:
        abort(403)

    if not book.is_text_available:
        abort(404, description="Текст книги недоступен")

    # Чтение файла с обработкой кодировки
    filepath = book.get_file_path()
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except UnicodeDecodeError:
        with open(filepath, 'rb') as f:
            encoding = chardet.detect(f.read())['encoding']
        with open(filepath, 'r', encoding=encoding) as f:
            content = f.read()

    # Разбивка на страницы (~1500 символов на страницу)
    pages = [content[i:i + 1500] for i in range(0, len(content), 1500)]

    # Проверка прогресса чтения
    progress = ReadingProgress.query.filter_by(
        user_id=current_user.id,
        book_id=book.id
    ).first()

    start_page = progress.current_page if progress else 1

    return render_template('books/reader.html',
                           book=book,
                           pages=pages,
                           current_page=start_page,
                           total_pages=len(pages))


@app.route('/book/<int:book_id>/content/<int:page>')
@login_required
def get_book_page(book_id, page):
    """API для получения конкретной страницы книги (AJAX)"""
    book = Book.query.get_or_404(book_id)

    if not book.is_text_available:
        return jsonify({"error": "Текст недоступен"}), 404

    filepath = book.get_file_path()
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except UnicodeDecodeError:
        with open(filepath, 'rb') as f:
            encoding = chardet.detect(f.read())['encoding']
        with open(filepath, 'r', encoding=encoding) as f:
            content = f.read()

    pages = [content[i:i + 1500] for i in range(0, len(content), 1500)]

    if page < 1 or page > len(pages):
        return jsonify({"error": "Неверный номер страницы"}), 400

    # Обновляем прогресс чтения
    progress = ReadingProgress.query.filter_by(
        user_id=current_user.id,
        book_id=book.id
    ).first()

    if not progress:
        progress = ReadingProgress(
            user_id=current_user.id,
            book_id=book.id,
            current_page=page
        )
        db.session.add(progress)
    else:
        progress.current_page = page
        progress.last_read = datetime.utcnow()

    db.session.commit()

    return jsonify({
        "content": pages[page - 1],
        "current_page": page,
        "total_pages": len(pages)
    })


@app.route('/book/<int:book_id>/download')
@login_required
def download_book(book_id):
    """Скачивание оригинального файла книги"""
    book = Book.query.get_or_404(book_id)

    if not book.is_text_available:
        abort(404)

    return send_from_directory(
        current_app.config['UPLOAD_FOLDER'],
        book.original_file,
        as_attachment=True,
        download_name=f"{book.title}.txt"
    )

# ---------------------------
# Инициализация приложения
# ---------------------------

if __name__ == '__main__':
    with app.app_context():
        db.create_all()

        # Создание администратора
        if not User.query.filter_by(username='admin').first():
            admin = User(
                username='admin',
                password_hash=generate_password_hash('admin'),
                is_admin=True
            )
            db.session.add(admin)
            db.session.commit()

    app.run(debug=True)