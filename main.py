import os
from http.server import HTTPServer, SimpleHTTPRequestHandler

PORT = int(os.environ.get("PORT", 8000))
HTTPServer(("0.0.0.0", PORT), SimpleHTTPRequestHandler).serve_forever()
