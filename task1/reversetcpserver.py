import socket
import struct
import threading
import sys
import datetime

# 日志
log_fp = None
log_lock = threading.Lock()

def init_log():
    global log_fp
    log_fp = open("run_log_server.txt", "w", encoding="utf-8")

def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
    line = f"[{ts}] {msg}"
    with log_lock:
        log_fp.write(line + "\n")
        log_fp.flush()
    print(line)

# 工具
def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError(
                f"连接断开（目标 {n} 字节，已读 {len(buf)} 字节）")
        buf += chunk
    return buf

# 参数校验
def input_check():
    if len(sys.argv) != 2:
        print("用法: python reversetcpserver.py <port>")
        sys.exit(1)
    try:
        port = int(sys.argv[1])
    except ValueError:
        print("错误：port 必须为整数")
        sys.exit(1)
    if not (1024 <= port <= 65535):
        print("错误：port 必须在 1024~65535 之间")
        sys.exit(1)
    return port

# 客户端处理
def client_handler(conn, address, num):
    tag = f"客户端{num}"
    try:
        # 1. 接收 Initialization (Type=1, N) 
        init_data = recv_exact(conn, 6)
        try:
            packet_type, n_chunks = struct.unpack('>HI', init_data)
        except struct.error:
            log(f"{tag} 初始化报文格式错误")
            return

        if packet_type != 1:
            log(f"{tag} 无效报文类型：期望 1，收到 {packet_type}")
            return
        if n_chunks <= 0:
            log(f"{tag} 块数必须为正整数")
            return
        log(f"{tag} ← 收到 Initialization (Type=1, N={n_chunks})")

        # 2. 发送 agree (Type=2) 
        conn.sendall(struct.pack('>H', 2))
        log(f"{tag} → 发送 agree (Type=2)")

        # 3. 逐块处理
        for i in range(1, n_chunks + 1):
            # 接收 reverseRequest 首部
            req_header = recv_exact(conn, 6)
            try:
                req_type, data_len = struct.unpack('>HI', req_header)
            except struct.error:
                log(f"{tag} 第 {i} 块请求头格式错误")
                return

            if req_type != 3:
                log(f"{tag} 无效报文类型：期望 3，收到 {req_type}")
                return

            # 接收数据
            data = recv_exact(conn, data_len)
            log(f"{tag} ← 收到 reverseRequest [{i}/{n_chunks}]  Length={data_len}")

            # 反转并发送 reverseAnswer
            reversed_data = data[::-1]
            answer_packet = struct.pack('>HI', 4, len(reversed_data)) + reversed_data
            conn.sendall(answer_packet)
            log(f"{tag} → 发送 reverseAnswer [{i}/{n_chunks}]  Length={len(reversed_data)}")

        log(f"{tag} ✔ 全部 {n_chunks} 块处理完成")

    except ConnectionError as e:
        log(f"{tag} 连接异常: {e}")
    except Exception as e:
        log(f"{tag} 未预期异常: {type(e).__name__}: {e}")
    finally:
        conn.close()
        log(f"{tag} 连接已关闭")

# 主入口
def create_server():
    port = input_check()
    init_log()

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        server_socket.bind(("0.0.0.0", port))
    except OSError as e:
        log(f"无法绑定端口 {port}: {e}") 
        sys.exit(1)

    server_socket.listen(5)
    log(f"服务端已启动！监听端口: {port}")
    log("正在等待客户端连接......")

    client_count = 0
    try:
        while True:
            conn, address = server_socket.accept()
            client_count += 1
            log(f"已接受客户端{client_count}号的连接请求，地址: {address}")
            thread = threading.Thread(
                target=client_handler,
                args=(conn, address, client_count),
                daemon=True
            )
            thread.start()
    except KeyboardInterrupt:
        log("收到 Ctrl+C，服务器退出")
    finally:
        server_socket.close()
        log_fp.close()

if __name__ == '__main__':
    create_server()