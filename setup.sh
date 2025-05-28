cp flask_app.nginx.conf /etc/nginx/sites-enabled/sunnyweather.nginx.conf
certbot --nginx -d sunnyweather.silaeder.codingprojects.ru
