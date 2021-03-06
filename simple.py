import http.cookies
import http.server
import socketserver
import random
import time
from urllib.parse import parse_qs
from cgi import parse_header, parse_multipart

import pyotp

PORT = 8000
SSL_STATUS = False # if set to True, cookies will only be set for SSL enabled connections
TOKEN_LIFETIME = 60 * 60 * 24
LAST_LOGIN_ATTEMPT = 0
REGISTERED = True
try:
    SECRET = open('.totp_secret').read().strip()
except Exception:
    SECRET = pyotp.random_base32() # returns a 16 character base32 secret. Compatible with Google Authenticator and other OTP apps
    REGISTERED = False
FORM = """
<html>
<head>
<title>Please Log In</title>
</head>
<body>
<form action="/auth/login" method="POST">
<input type="text" name="token">
<input type="submit" value="Submit">
</form>
</body>
</html>
"""

class TokenManager(object):
    """Who needs a database when you can just store everything in memory?"""

    def __init__(self):
        self.tokens = {}
        self.random = random.SystemRandom()

    def generate(self):
        t = '%064x' % self.random.getrandbits(8*32)
        self.tokens[t] = time.time()
        return t

    def is_valid(self, t):
        try:
            return time.time() - self.tokens.get(t, 0) < TOKEN_LIFETIME
        except Exception:
            return False

    def invalidate(self, t):
        if t in self.tokens:
            del self.tokens[t]

TOKEN_MANAGER = TokenManager()

class AuthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        global REGISTERED
        if self.path == '/auth/check':
            # Check if they have a valid token
            cookie = http.cookies.SimpleCookie(self.headers.get('Cookie'))
            if 'token' in cookie and TOKEN_MANAGER.is_valid(cookie['token'].value):
                self.send_response(200)
                self.end_headers()
                return

            # Otherwise return 401, which will be redirected to '/auth/login' upstream
            self.send_response(401)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(bytes(FORM, 'UTF-8'))
            return

        if self.path == '/auth/register':
            # Render out the register form
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            if REGISTERED == False:
                REGISTERED = True
                with open('.totp_secret', 'w') as f:
                    f.write(SECRET)
                QR = '<img src=https://chart.googleapis.com/chart?chs=200x200&chld=M%7C0&cht=qr&chl=otpauth://totp/test@test%3Fsecret='+str(SECRET)+'>TOTP SECRET: '+str(SECRET)+'</img>'
                self.wfile.write(bytes(QR, 'UTF-8'))
            else:
                self.wfile.write(bytes('Client already registered', 'UTF-8'))
            return

        if self.path == '/auth/logout':
            # Invalidate any tokens
            cookie = http.cookies.SimpleCookie(self.headers.get('Cookie'))
            if 'token' in cookie:
                TOKEN_MANAGER.invalidate(cookie['token'].value)

            # This just replaces the token with garbage
            self.send_response(302)
            cookie = http.cookies.SimpleCookie()
            cookie["token"] = '***'
            cookie["token"]["path"] = '/'
            cookie["token"]["secure"] = SSL_STATUS
            self.send_header('Set-Cookie', cookie.output(header=''))
            self.send_header('Location', '/auth/check')
            self.end_headers()
            return

        # Otherwise return 404
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        if self.path == '/auth/login':
            # Rate limit login attempts to once per second
            global LAST_LOGIN_ATTEMPT
            if time.time() - LAST_LOGIN_ATTEMPT < 1.0:
                self.send_response(429)
                self.end_headers()
                self.wfile.write(bytes('Slow down. Hold your horses', 'UTF-8'))
                return
            LAST_LOGIN_ATTEMPT = time.time()

            # Check the TOTP Secret
            params = self.parse_POST()
            if (params.get(b'token') or [None])[0] == bytes(pyotp.TOTP(SECRET).now(), 'UTF-8'):
                cookie = http.cookies.SimpleCookie()
                cookie["token"] = TOKEN_MANAGER.generate()
                cookie["token"]["path"] = "/"
                cookie["token"]["secure"] = SSL_STATUS

                self.send_response(302)
                self.send_header('Set-Cookie', cookie.output(header=''))
                self.send_header('Location', '/auth/check')
                self.end_headers()
                return

            # Otherwise redirect back to the auth check page
            else:
                self.send_response(302)
                self.send_header('Location', '/auth/check')
                self.end_headers()
                return

        # Otherwise return 404
        self.send_response(404)
        self.end_headers()

    def parse_POST(self):
        """Lifted from https://stackoverflow.com/questions/4233218/"""
        ctype, pdict = parse_header(self.headers['content-type'])
        if ctype == 'multipart/form-data':
            postvars = parse_multipart(self.rfile, pdict)
        elif ctype == 'application/x-www-form-urlencoded':
            length = int(self.headers['Content-Length'])
            postvars = parse_qs( self.rfile.read(length), keep_blank_values=1)
        else:
            postvars = {}
        return postvars

socketserver.TCPServer.allow_reuse_address = True
httpd = socketserver.TCPServer(("", PORT), AuthHandler)
try:
    print("serving at port", PORT)
    httpd.serve_forever()
finally:
    httpd.server_close()
