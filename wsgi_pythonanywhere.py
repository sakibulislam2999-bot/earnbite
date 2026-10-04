# Paste this into your PythonAnywhere WSGI file (Web tab -> WSGI configuration file)
import os, sys
sys.path.insert(0, "/home/YOURNAME/teleads/backend")
os.environ["BOT_TOKEN"] = "123456:YOUR_BOT_TOKEN"            # from @BotFather
os.environ["ADMIN_USER"] = "admin"
os.environ["ADMIN_PASSWORD"] = "PUT-A-LONG-PASSWORD-HERE"
os.environ["SECRET_KEY"] = "put-any-long-random-text-here"
os.environ["ALLOWED_ORIGIN"] = "https://YOURGITHUB.github.io"  # your GitHub Pages origin
from app import app as application
