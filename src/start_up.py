import http.server
import socketserver
import schedule
import time
import os
from config import PORT


# class Handler(http.server.SimpleHTTPRequestHandler):
#     def translate_path(self, path):
#         # 将请求路径映射到文件系统路径
#         # 例如: 将 /images/photo.jpg 映射到 ./images/photo.jpg
#         return super().translate_path(path[1:])


# # 创建 HTTP 服务器
# with socketserver.TCPServer(("", PORT), Handler) as httpd:
#     print(f"服务器已启动，监听端口 {PORT}")
#     httpd.serve_forever()


def job():
    os.system("python bilibili_channel.py")
    os.system("python upload_r2.py")


job()

schedule.every(5).hour.do(job)
while True:
    schedule.run_pending()
    time.sleep(10)
