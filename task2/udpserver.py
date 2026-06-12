import socket
import struct
import sys
import random
import datetime
import threading
import queue

# 1.自定义应用层协议
VERSION   = 1
FLAG_SYN  = 0x1
FLAG_ACK  = 0x2
FLAG_FIN  = 0x4
FLAG_DATA = 0x8

HEADER_FMT  = "!BBHIIHH"
HEADER_SIZE = struct.calcsize(HEADER_FMT)

MAX_PAYLOAD     = 80
STUDENT_XOR_KEY = 0x5A3C

def checksum16(data: bytes) -> int:
    if len(data) % 2 == 1: # 校验和标准规定把整个报文看成一系列16位字进行
        data = data + b"\x00"
    s = 0
    for i in range(0, len(data), 2):
        s += (data[i] << 8) | data[i + 1]
        s = (s & 0xFFFF) + (s >> 16) # 回卷，加完不会再产生新进位（不会二次溢出）
    return (~s) & 0xFFFF

def pack_msg(flags, student_id=0, seq_byte_start=0, ack_byte_end=0, payload=b""):
    ver_flags = ((VERSION & 0xF) << 4) | (flags & 0xF)
    payload_len = len(payload)
    header = struct.pack(HEADER_FMT,
                         ver_flags, 0, student_id,
                         seq_byte_start, ack_byte_end,
                         payload_len, 0)
    ck = checksum16(header + payload) # 按照校验和为0计算的
    header = header[:-2] + struct.pack("!H", ck) # 转成二字节的二进制数据
    return header + payload

def unpack_msg(raw: bytes):
    """client→server 不可靠，需要完整校验"""
    if raw is None or len(raw) < HEADER_SIZE:
        return None
    try:
        (ver_flags, _rsv, student_id,
         seq_byte_start, ack_byte_end,
         payload_len, recv_ck) = struct.unpack(HEADER_FMT, raw[:HEADER_SIZE])
    except struct.error: # 如格式不匹配
        return None
    version = (ver_flags >> 4) & 0xF
    flags = ver_flags & 0xF
    if version != VERSION: return None
    if payload_len > MAX_PAYLOAD: return None
    if len(raw) != HEADER_SIZE + payload_len: return None
    header_zero_ck = raw[:HEADER_SIZE - 2] + b"\x00\x00" # 客户端计算校验和时该字段为0
    if checksum16(header_zero_ck + raw[HEADER_SIZE:]) != recv_ck: return None
    return {
        "flags": flags,
        "student_id": student_id,
        "seq_byte_start": seq_byte_start,
        "ack_byte_end": ack_byte_end,
        "payload_len": payload_len,
        "payload": raw[HEADER_SIZE:],
    }

# 2.日志
log_fp = None
log_lock = threading.Lock()   # 互斥锁，Session线程之间会并发写日志，防止log错乱

def init_log():
    global log_fp
    log_fp = open("run_log_server.txt", "w", encoding="utf-8")

def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
    line = f"[{ts}] {msg}"
    with log_lock: # with最后自动释放锁
        log_fp.write(line + "\n")
        log_fp.flush() # 防止程序崩溃日志丢失
        print(line)

# 3.参数校验
def input_check():
    if len(sys.argv) < 2: # 程序名 + 端口号
        print("用法: python udpserver.py <port> [loss_rate] [corrupt_rate]"); sys.exit(1)
    try:
        port = int(sys.argv[1])
    except ValueError:
        print("错误：port 必须为整数"); sys.exit(1)
    if not (1024 <= port <= 65535):
        print("错误：port 必须在 1024~65535 之间"); sys.exit(1)

    loss_rate = 0.2
    corrupt_rate = 0.1
    if len(sys.argv) >= 3:
        try:
            loss_rate = float(sys.argv[2])
        except ValueError:
            print("错误：loss_rate 必须为浮点数"); sys.exit(1)
        if not (0.0 <= loss_rate < 1.0):
            print("错误：loss_rate 必须在 [0.0, 1.0) 之间"); sys.exit(1)
    if len(sys.argv) >= 4:
        try:
            corrupt_rate = float(sys.argv[3])
        except ValueError:
            print("错误：corrupt_rate 必须为浮点数"); sys.exit(1)
        if not (0.0 <= corrupt_rate < 1.0):
            print("错误：corrupt_rate 必须在 [0.0, 1.0) 之间"); sys.exit(1)
    if loss_rate + corrupt_rate >= 1.0:
        print("错误：loss_rate + corrupt_rate 必须 < 1.0"); sys.exit(1)
    return port, loss_rate, corrupt_rate

# 4.客户端会话（每个client一个独立线程）
class ClientSession:
    """
    每个 client 一个 Session 线程，独立完成：握手 → 数据 → 挥手。
    主线程 (dispatcher) 通过 self.submit() 把属于本会话的报文塞进队列。
    """
    def __init__(self, addr, sock, loss_rate):
        self.addr = addr # 客户端 (IP, port)
        self.sock = sock # 服务端共享的 UDP socket
        self.loss_rate = loss_rate
        self.queue = queue.Queue() # 本会话消息队列
        self.thread = threading.Thread(target=self.run, daemon=True, name=f"Session-{addr}") # 真正的线程，daemon：守护线程，主进程退出自动销毁
        self.tag = f"[Sess {addr[0]}:{addr[1]}]" # 日志标签

    def start(self):       self.thread.start() 
    def submit(self, pkt): self.queue.put(pkt)
    def alive(self):       return self.thread.is_alive()
    def _send(self, raw):  self.sock.sendto(raw, self.addr) # 向客户端发送原始字节流

    def run(self):
        try:
            log(f"{self.tag} 会话线程启动")
            # 三次握手
            if not self._handshake():
                return
            # 数据传输
            if not self._receive_data():
                return
            # 四次挥手
            self._teardown()
            log(f"{self.tag} 会话正常结束")
        except Exception as e:
            log(f"{self.tag} 会话异常: {type(e).__name__}: {e}")
    
    def _handshake(self):
        """三次握手：收 SYN → 回 SYN+ACK → 收 ACK"""
        try:
            pkt = self.queue.get(timeout=5)
        except queue.Empty:
            log(f"{self.tag} 握手等 SYN 超时")
            return False
        if pkt["flags"] != FLAG_SYN:
            log(f"{self.tag} 首包非 SYN，关闭")
            return False

        field = pkt["student_id"]
        real_id = field ^ STUDENT_XOR_KEY
        if not (0 <= real_id <= 9999):
            log(f"{self.tag} ✘ 学号非法 0x{field:04X} → {real_id}")
            return False
        log(f"{self.tag} ← SYN  学号字段=0x{field:04X} → "
            f"学号后4位={real_id:04d}（合法）")

        self._send(pack_msg(FLAG_SYN | FLAG_ACK))
        log(f"{self.tag} → SYN+ACK")

        try:
            pkt = self.queue.get(timeout=5)
        except queue.Empty:
            log(f"{self.tag} 握手等第三步 ACK 超时")
            return False
        if pkt["flags"] != FLAG_ACK:
            log(f"{self.tag} 握手第三步非 ACK，关闭")
            return False
        log(f"{self.tag} ← ACK，连接建立完成")
        return True

    def _receive_data(self):
        expected_byte = 1 # 下一次期望收到的字节在文件中的起始编号
        ack_byte_end = 0
        log(f"{self.tag} 进入数据传输阶段，模拟丢包率 = {self.loss_rate*100:.1f}%")

        while True:
            try:
                pkt = self.queue.get(timeout=60)
            except queue.Empty:
                log(f"{self.tag} 数据阶段 60s 无报文，超时关闭"); return False

            flags = pkt["flags"]
            if flags == FLAG_FIN:
                log(f"{self.tag} ← 收到 FIN，进入挥手阶段")
                return True
            if flags != FLAG_DATA:
                log(f"{self.tag} ← 非 DATA 报文 flags=0x{flags:X}，丢弃")
                continue

            seq_start = pkt["seq_byte_start"]
            plen = pkt["payload_len"]
            seq_end = seq_start + plen - 1

            # 随机丢弃
            if random.random() < self.loss_rate:
                log(f"{self.tag} ✘ [模拟丢包] 第 {seq_start}~{seq_end} 字节，不响应")
                continue

            if seq_start == expected_byte:
                ack_byte_end = seq_end
                expected_byte = seq_end + 1
                log(f"{self.tag} ← 第 {seq_start}~{seq_end} 字节 "
                    f"按序到达，累计 ACK 至 {ack_byte_end}")
            else:
                log(f"{self.tag} ← 第 {seq_start}~{seq_end} 字节 失序到达"
                    f"（期望从 {expected_byte} 开始），丢弃 + 重发累计 ACK")

            server_time = datetime.datetime.now().strftime("%H-%M-%S")
            self._send(pack_msg(FLAG_ACK,
                                ack_byte_end=ack_byte_end,
                                payload=server_time.encode("ascii")))
            log(f"{self.tag} → 累计 ACK  ack_byte_end={ack_byte_end}  "
                f"server_time={server_time}")

    def _teardown(self):
        self._send(pack_msg(FLAG_ACK))
        log(f"{self.tag} → ACK（对 FIN 的确认）")

        self._send(pack_msg(FLAG_FIN))
        log(f"{self.tag} → FIN")

        try:
            pkt = self.queue.get(timeout=5) # 队列中取出(并移除)一个报文字典
            if pkt["flags"] == FLAG_ACK:
                log(f"{self.tag} ← 收到最后的 ACK，连接关闭完成")
            else:
                log(f"{self.tag} 挥手最后一步收到非 ACK 报文 flags=0x{pkt['flags']:X}")
        except queue.Empty:
            log(f"{self.tag} ✘ 等待最后 ACK 超时")

# 5.主入口（dispatcher 线程）
def create_server():
    port, loss_rate, corrupt_rate = input_check()
    init_log()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM) # IPv4地址+UDP协议
    try:
        sock.bind(("0.0.0.0", port)) # 绑定到 0.0.0.0:port，监听本机所有网络接口。
    except OSError as e:
        log(f"无法绑定端口 {port}: {e}"); sys.exit(1)

    log(f"服务端已启动！监听端口: {port}  "
        f"模拟丢包率: {loss_rate*100:.1f}%  模拟损坏率: {corrupt_rate*100:.1f}%")
    log(f"[多线程] 主线程 = dispatcher，每个 client 独立 Session 线程")
    log(f"[不可靠模拟] 仅作用于 client→server 方向的 DATA 报文；"
        f"握手/挥手/server→client 假设可靠")

    sessions = {} # 字典：客户端地址 → ClientSession 对象

    try:
        while True: # 除非被Ctrl+C或者异常打断
            try:
                raw, addr = sock.recvfrom(2048) # addr:IP+port
            except OSError:
                break

            '''模拟比特损坏：仅对"已建立会话"的 DATA 报文'''
            # 在unpack前翻转1个bit，触发unpack_msg里的checksum校验失败。
            corrupted_here = False # 是否主动注入损坏
            corrupt_seq_info = "" # 范围
            if (len(raw) >= HEADER_SIZE
                    and (raw[0] & 0xF) == FLAG_DATA
                    and addr in sessions
                    and random.random() < corrupt_rate):
                try:
                    seq_peek = struct.unpack("!I", raw[4:8])[0]
                    len_peek = struct.unpack("!H", raw[12:14])[0]
                    corrupt_seq_info = f"第 {seq_peek}~{seq_peek + len_peek - 1} 字节"
                except struct.error:
                    corrupt_seq_info = "(seq 解析失败)"
                ba = bytearray(raw) # bytes不可变,无法直接修改
                byte_idx = random.randrange(len(ba))
                bit_idx = random.randrange(8)
                ba[byte_idx] ^= 1 << bit_idx # 单bit异或
                raw = bytes(ba)
                corrupted_here = True
                log(f"✘ [注入损坏] {addr} 的 DATA {corrupt_seq_info}，"
                    f"翻转 byte#{byte_idx} bit#{bit_idx}")

            pkt = unpack_msg(raw)
            if pkt is None:
                if corrupted_here:
                    log(f"✘ [模拟损坏] 来自 {addr} 的 DATA {corrupt_seq_info}，"
                        f"checksum 校验失败 → 丢弃且不响应")
                else:
                    log(f"← 收到无效报文（来自 {addr}），丢弃")
                continue

            # 先清理已结束会话
            for a in [a for a, s in sessions.items() if not s.alive()]:
                log(f"[Dispatcher] 清理已结束会话 {a}")
                del sessions[a]
            # 再分发
            if addr in sessions:
                sessions[addr].submit(pkt)
            elif pkt["flags"] == FLAG_SYN:
                log(f"[Dispatcher] 新会话来自 {addr}，启动 Session 线程")
                s = ClientSession(addr, sock, loss_rate)
                sessions[addr] = s
                s.start()
                s.submit(pkt)
            else:
                log(f"← 未知来源 {addr} 非 SYN 报文 "
                    f"flags=0x{pkt['flags']:X}，丢弃")
    except KeyboardInterrupt:
        log("收到 Ctrl+C，服务器退出")
    except Exception as e:
        log(f"未预期异常: {type(e).__name__}: {e}")
    finally:
        sock.close()
        if log_fp:
            log_fp.close()

if __name__ == '__main__':
    create_server()