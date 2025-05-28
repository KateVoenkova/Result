cp flask_app.nginx.conf /etc/nginx/sites-enabled/weather.nginx.conf
certbot --nginx -d weather.silaeder.codingprojects.ru
