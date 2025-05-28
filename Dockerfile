FROM python:3.10-slim

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем файл со списком зависимостей и устанавливаем их + Gunicorn
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN apt-get update && apt-get install -y dos2unix

# Копируем остальной код
COPY . .
RUN dos2unix /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Открываем порт (по умолчанию 5000 для Flask)
EXPOSE 1199

# Запускаем Gunicorn, указывая модуль и приложение (app:app)
WORKDIR /app
CMD ["sh", "/app/entrypoint.sh"]