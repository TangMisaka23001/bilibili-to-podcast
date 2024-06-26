import http.server
import socketserver
import yaml
import schedule
import time
import os

PORT = 8000

def load_config():
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)
        global PORT
        PORT = config["PORT"]

class Handler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        # 将请求路径映射到文件系统路径
        # 例如: 将 /images/photo.jpg 映射到 ./images/photo.jpg
        return super().translate_path(path[1:])
    
load_config()
# 创建 HTTP 服务器
with socketserver.TCPServer(("", PORT), Handler) as httpd:
    print(f"服务器已启动，监听端口 {PORT}")
    httpd.serve_forever()

schedule.every(10).hour.do(os.system('python bilibili_channel.py')) 
while True:
    schedule.run_pending()
    time.sleep(10)