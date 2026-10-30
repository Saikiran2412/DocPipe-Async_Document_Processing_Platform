import hashlib
import hmac
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

SECRET = sys.argv[1] if len(sys.argv) > 1 else "whsec_dev_change_me"
TOLERANCE = 300          # seconds
seen: dict[str, int] = {}


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code: int) -> None:
        self.send_response(code)
        self.end_headers()

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        delivery = self.headers.get("X-Docpipe-Delivery", "?")
        timestamp = self.headers.get("X-Docpipe-Timestamp", "")
        signature = self.headers.get("X-Docpipe-Signature", "")

        expected = "sha256=" + hmac.new(
            SECRET.encode(), timestamp.encode() + b"." + body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            print(f"[401] bad signature        {delivery}")
            return self._reply(401)
        try:
            age = abs(time.time() - int(timestamp))
        except ValueError:
            age = TOLERANCE + 1
        if age > TOLERANCE:
            print(f"[401] stale timestamp      {delivery}")
            return self._reply(401)

        seen[delivery] = seen.get(delivery, 0) + 1
        n = seen[delivery]
        data = json.loads(body)

        if self.path == "/reject":
            print(f"[400] refusing             {delivery}")
            return self._reply(400)
        if self.path == "/down" or (self.path == "/flaky" and n <= 2):
            print(f"[500] simulated failure #{n}  {delivery}")
            return self._reply(500)

        print(f"[200] OK  {data['event']}  status={data['job']['status']}  delivery={delivery}  (request #{n})")
        self._reply(200)

    def log_message(self, *args):
        pass


print("receiver on http://127.0.0.1:9100  (paths: /ok /flaky /down /reject)")
HTTPServer(("127.0.0.1", 9100), Handler).serve_forever()
