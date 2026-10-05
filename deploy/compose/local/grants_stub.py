"""DOBLE LOCAL de la plataforma para `grant_active`: toda delegación firmada cuenta como vigente.

Solo para la prueba local con `docker-compose.local.yml`. Responde `GET /api/v1/internal/grants/<ref>` con
`{"active": true}` si trae el Bearer esperado (`GRANTS_TOKEN`); sin él, 401. No existe en producción: ahí la
plataforma real decide, y una revocación solo se ve con ella."""

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

TOKEN = os.environ["GRANTS_TOKEN"]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self.send_response(401)
            self.end_headers()
            return
        body = json.dumps({"active": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:  # sin log de acceso
        return


HTTPServer(("0.0.0.0", 8099), Handler).serve_forever()
