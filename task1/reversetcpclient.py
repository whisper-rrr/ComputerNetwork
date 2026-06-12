import socket
import struct
import random
import sys
import datetime

# 日志
log_fp = None

def init_log():
    global log_fp
    log_fp = open("run_log.txt", "w", encoding="utf-8")


def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
    line = f"[{ts}] {msg}"
    log_fp.write(line + "\n")
    log_fp.flush()
    print(line)

# 工具
def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError(f"连接断开（目标 {n} 字节，已读 {len(buf)} 字节）")
        buf += chunk
    return buf

# 参数校验
def input_check():
    if len(sys.argv) < 6:
        print("用法: python reversetcpclient.py "
              "<server_ip> <server_port> <file_path> <Lmin> <Lmax> [seed]")
        sys.exit(1)

    server_ip = sys.argv[1]
    server_port = int(sys.argv[2])
    file_path = sys.argv[3]
    Lmin = int(sys.argv[4])
    Lmax = int(sys.argv[5])
    seed = int(sys.argv[6]) if len(sys.argv) > 6 else None

    if not (1024 <= server_port <= 65535):
        print("错误：端口必须在 1024~65535 之间")
        sys.exit(1)
    if Lmin <= 0 or Lmax <= 0 or Lmin > Lmax:
        print("错误：请输入合法的 Lmin 和 Lmax")
        sys.exit(1)

    return server_ip, server_port, file_path, Lmin, Lmax, seed

# 文件读取
def get_data_from_file(file_path):
    try:
        with open(file_path, 'rb') as f:
            data = f.read()
    except Exception as e:
        print(f"读取文件错误: {e}")
        sys.exit(1)
    if not data:
        print("错误：文件为空")
        sys.exit(1)
    return data

# 分块
def split_chunks(data, Lmin, Lmax, seed=None):
    """
    随机分块算法：
      - remaining > Lmax  → chunk_size = randint(Lmin, Lmax)
      - remaining <= Lmax → 最后一块取全部（可能 < Lmin，最后一块豁免）
    """
    if seed is not None:
        random.seed(seed)

    chunks = []
    offset = 0
    total = len(data)

    while offset < total:
        remaining = total - offset
        if remaining <= Lmax:
            chunks.append(data[offset:])
            break
        chunk_size = random.randint(Lmin, Lmax)
        chunks.append(data[offset:offset + chunk_size])
        offset += chunk_size

    return chunks

# 主流程
def create_client():
    server_ip, server_port, file_path, Lmin, Lmax, seed = input_check()
    init_log()
    log(f"参数: server={server_ip}:{server_port}  file={file_path}  "
        f"Lmin={Lmin}  Lmax={Lmax}  seed={seed}")

    # 1. 读取文件
    data = get_data_from_file(file_path)
    log(f"读取文件 '{file_path}'，大小 {len(data)} 字节")

    # 2. 分块
    chunks = split_chunks(data, Lmin, Lmax, seed)
    n_chunks = len(chunks)
    log(f"分块完成: N={n_chunks}")
    offset = 0
    for idx, c in enumerate(chunks):
        log(f"  chunk[{idx + 1}]: offset={offset}  length={len(c)}")
        offset += len(c)

    # 3. 建立TCP连接
    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client_socket.settimeout(10.0)
    try:
        client_socket.connect((server_ip, server_port))
        log(f"已连接到 {server_ip}:{server_port}")
    except Exception as e:
        log(f"连接失败: {e}")
        client_socket.close()
        return

    try:
        # 4. 发送 Initialization (Type=1, N)
        init_packet = struct.pack('>HI', 1, n_chunks)
        client_socket.sendall(init_packet)
        log(f"→ 发送 Initialization (Type=1, N={n_chunks})")

        # 5. 接收 agree (Type=2)
        agree_data = recv_exact(client_socket, 2)
        agree_type, = struct.unpack('>H', agree_data)
        if agree_type != 2:
            log(f"协议错误：期望 Type=2，收到 Type={agree_type}")
            return
        log(f"← 收到 agree (Type=2)")

        # 6. 逐块 reverseRequest / reverseAnswer
        reversed_chunks = []
        for i, chunk in enumerate(chunks):
            # 发送 reverseRequest (Type=3, Length, Data)
            request_packet = struct.pack('>HI', 3, len(chunk)) + chunk
            client_socket.sendall(request_packet)
            log(f"→ 发送 reverseRequest [{i + 1}/{n_chunks}]  Length={len(chunk)}")

            # 接收 reverseAnswer 首部 (Type=4, Length)
            answer_header = recv_exact(client_socket, 6)
            answer_type, answer_length = struct.unpack('>HI', answer_header)
            if answer_type != 4:
                log(f"协议错误：期望 Type=4，收到 Type={answer_type}")
                return

            # 接收反转数据
            reversed_data = recv_exact(client_socket, answer_length)
            log(f"← 收到 reverseAnswer [{i + 1}/{n_chunks}]  Length={answer_length}")

            # 打印
            try:
                text = reversed_data.decode('ascii')
            except UnicodeDecodeError:
                text = repr(reversed_data)
            print(f"第 {i + 1} 块: {text}")

            reversed_chunks.insert(0, reversed_data)

        log(f"✔ 全部 {n_chunks} 块处理完成")

        # 7. 保存完整反转文件
        output_file = 'reversed_output.txt'
        with open(output_file, 'wb') as f:
            for rc in reversed_chunks:
                f.write(rc)
        log(f"反转文件已保存到 '{output_file}'")

        # 8. 自动校验
        expected = data[::-1]
        actual = b"".join(reversed_chunks)
        if actual == expected:
            log("✔ 校验通过：反转结果与原文件的 reverse 完全一致")
        else:
            log("✘ 校验失败！反转结果与预期不符")
            for pos, (a, b) in enumerate(zip(actual, expected)):
                if a != b:
                    log(f"  首个差异位置: {pos}  输出=0x{a:02X}  期望=0x{b:02X}")
                    break

    except ConnectionError as e:
        log(f"连接异常: {e}")
    except Exception as e:
        log(f"未预期异常: {type(e).__name__}: {e}")
    finally:
        client_socket.close()
        log("连接已关闭")
        log_fp.close()

def main():
    create_client()

if __name__ == '__main__':
    main()