# gunicorn.conf.py
bind = "127.0.0.1:5070"       # listen on localhost; front with a proxy if you like
workers = 1                   # IMPORTANT: 1 process so you don’t duplicate the scroller
threads = 8                   # concurrent requests
worker_class = "gthread"      # simple + works great for I/O
timeout = 60
graceful_timeout = 30
keepalive = 5
accesslog = "-"               # stdout
errorlog = "-"                # stderr
loglevel = "info"

