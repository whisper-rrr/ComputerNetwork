import socket
import struct
import sys
import time
import random
import datetime
import threading
import pandas as pd

# 1.自定义应用层协议

# 16 字节首部
# Ver|Flg(1) Rsv(1) StudentID(2)
# SeqStart(4)
# AckEnd(4)
# PayloadLen(2) Checksum(2)

VERSION   = 1
FLAG_SYN  = 0x1
FLAG_ACK  = 0x2
FLAG_FIN  = 0x4
FLAG_DATA = 0x8

HEADER_FMT  = "!BBHIIHH"
HEADER_SIZE = struct.calcsize(HEADER_FMT) 

MIN_PAYLOAD = 40
MAX_PAYLOAD = 80

STUDENT_ID_LAST4 = 5126
STUDENT_XOR_KEY  = 0x5A3C
STUDENT_ID_FIELD = STUDENT_ID_LAST4 ^ STUDENT_XOR_KEY

def checksum16(data: bytes) -> int:
    if len(data) % 2 == 1:
        data = data + b"\x00"
    s = 0
    for i in range(0, len(data), 2):
        s += (data[i] << 8) | data[i + 1]
        s = (s & 0xFFFF) + (s >> 16)
    return (~s) & 0xFFFF

def pack_msg(flags, student_id=0, seq_byte_start=0, ack_byte_end=0, payload=b""):
    ver_flags   = ((VERSION & 0xF) << 4) | (flags & 0xF)
    payload_len = len(payload)
    header = struct.pack(HEADER_FMT,
                         ver_flags, 0, student_id,
                         seq_byte_start, ack_byte_end,
                         payload_len, 0)
    ck = checksum16(header + payload)
    header = header[:-2] + struct.pack("!H", ck)
    return header + payload

def unpack_msg(raw: bytes): # 假定可靠，无需完整校验，保留的if和try仅用于防止崩溃
    if raw is None or len(raw) < HEADER_SIZE: return None
    try:
        (ver_flags, _rsv, student_id,
         seq_byte_start, ack_byte_end,
         payload_len, recv_ck) = struct.unpack(HEADER_FMT, raw[:HEADER_SIZE])
    except struct.error:
        return None
    flags = ver_flags & 0xF
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
log_lock = threading.Lock()   # 还有个超时重传线程,防止此线程触发log

def init_log():
    global log_fp
    log_fp = open("run_log.txt", "w", encoding="utf-8")

def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
    line = f"[{ts}] {msg}"
    with log_lock:
        log_fp.write(line + "\n")
        log_fp.flush()
        print(line)

# 3.参数校验
def input_check():
    if len(sys.argv) < 3:
        print("用法: python udpclient.py <server_ip> <server_port> [seed]") # seed:可复现丢包
        sys.exit(1)
    server_ip = sys.argv[1]
    try:
        server_port = int(sys.argv[2])
    except ValueError:
        print("错误：port 必须为整数"); sys.exit(1)
    if not (1024 <= server_port <= 65535):
        print("错误：port 必须在 1024~65535 之间"); sys.exit(1)
    seed = None
    if len(sys.argv) >= 4:
        try:
            seed = int(sys.argv[3])
        except ValueError:
            print("错误：seed 必须为整数"); sys.exit(1)
    return server_ip, server_port, seed

# 4.配置
N_PACKETS         = 30          # 总共发送30个数据包
WINDOW_BYTES      = 400         # 发送窗口大小（字节）
INITIAL_TIMEOUT_S = 0.3         # 初始超时时间 300ms
DUP_ACK_THRESHOLD = 3           # 快重传重复ACK阈值
ALPHA, BETA = 0.125, 0.25       # RTT估计平滑因子
TIMEOUT_MIN_S, TIMEOUT_MAX_S = 0.05, 2.0  # RTO的上下限

# 5.自适应超时（单线程访问无需锁）
class AdaptiveTimer: # 动态计算,让协议能适应网络变化
    """RTO = EstRTT + 4·DevRTT；只由主线程读写。"""
    def __init__(self):
        self.timeout = INITIAL_TIMEOUT_S
        self.estimated_rtt = None # 往返时间评估
        self.dev_rtt = None # 往返时间偏差

    def update(self, sample_rtt_s):
        if self.estimated_rtt is None:
            self.estimated_rtt = sample_rtt_s
            self.dev_rtt = sample_rtt_s / 2.0
        else:
            self.estimated_rtt = (1 - ALPHA) * self.estimated_rtt + ALPHA * sample_rtt_s
            self.dev_rtt = (1 - BETA) * self.dev_rtt + BETA * abs(sample_rtt_s - self.estimated_rtt)
        new_to = self.estimated_rtt + 4 * self.dev_rtt # 新超时间隔
        self.timeout = max(TIMEOUT_MIN_S, min(new_to, TIMEOUT_MAX_S)) # 超时时间限制限制在区间内,防止极端值导致协议工作异常(过快/慢)

# 6.三次握手 / 四次挥手
def handshake(sock, server_addr):
    try:
        sock.sendto(pack_msg(FLAG_SYN, student_id=STUDENT_ID_FIELD), server_addr)
        log(f"→ 发送 SYN  StudentID字段=0x{STUDENT_ID_FIELD:04X}") # 左侧补0

        sock.settimeout(2.0)
        raw, _ = sock.recvfrom(2048)
        pkt = unpack_msg(raw)
        if pkt is None or pkt["flags"] != (FLAG_SYN | FLAG_ACK):
            log("✘ 握手第二步报文非预期"); return False
        log("← 收到 SYN+ACK")

        sock.sendto(pack_msg(FLAG_ACK), server_addr)
        log("→ 发送 ACK，连接建立完成")
        return True
    except (socket.timeout, OSError) as e:
        log(f"✘ 握手失败: {e}"); return False

def teardown(sock, server_addr):
    try:
        sock.settimeout(2.0)
        sock.sendto(pack_msg(FLAG_FIN), server_addr); log("→ 发送 FIN")
        sock.recvfrom(2048); log("← 收到 ACK（对 FIN 的确认）")
        sock.recvfrom(2048); log("← 收到 FIN")
        sock.sendto(pack_msg(FLAG_ACK), server_addr); log("→ 发送 ACK，连接关闭完成")
        return True
    except (socket.timeout, OSError) as e:
        log(f"✘ 挥手失败: {e}"); return False

# 7.数据准备
def prepare_packets():
    pool = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    packets = []
    offset = 1 # 起始字节序号,0可能有特殊用途
    for _ in range(N_PACKETS):
        plen = random.randint(MIN_PAYLOAD, MAX_PAYLOAD)
        payload = bytes(random.choices(pool, k=plen)) # 列表转化为字节串
        packets.append({"seq_start": offset, "seq_end": offset + plen - 1,
                        "payload": payload, "len": plen})
        offset += plen
    return packets

# 8.GBN 发送（单线程 + 自适应超时 + 快重传）
def gbn_send(sock, server_addr, packets):
    rtt_timer = AdaptiveTimer()        # 自适应 RTO 计时器
    base = 1                           # GBN 窗口基序号（最小未确认的包编号）
    next_seq = 1                       # 下一个将要发送的包编号
    timer_start = None                 # 定时器启动时间
    first_send_time = {}               # 记录每个包第一次发出的时刻，用于 RTT 采样
    retransmitted = set()              # 记录哪些包序号的包已经被重传过（Karn 算法）
    rtts = []                          # 收集 RTT 样本
    total_send = 0                     # 实际发送的包数（含重传）
    last_ack_end = None                # 上一次收到的 ACK 字节序号，用于检测重复 ACK
    dup_ack_count = 0                  # 连续重复 ACK 计数
    in_recovery = False                # 是否已进入快恢复状态
    fast_retx_count = 0                # 快重传触发次数
    timeout_retx_count = 0             # 超时重传触发次数

    def do_retransmit(reason):
        nonlocal total_send
        log(f"⚠ {reason}！重传 base={base}..{next_seq - 1}（共 {next_seq - base} 个）")
        for i in range(base, next_seq):
            p = packets[i - 1]
            sock.sendto(pack_msg(FLAG_DATA,
                                 seq_byte_start=p["seq_start"],
                                 payload=p["payload"]), server_addr)
            total_send += 1
            retransmitted.add(i)
            log(f"→ 重传第 {i} 个（第 {p['seq_start']}~{p['seq_end']} 字节）数据包")

    while base <= N_PACKETS:
        # 1. 填窗口
        while next_seq <= N_PACKETS:
            window_used = sum(packets[i-1]["len"] for i in range(base, next_seq))
            if window_used + packets[next_seq-1]["len"] > WINDOW_BYTES: # 窗口大小限制是字节数
                break
            p = packets[next_seq - 1] # 发送第一个包
            sock.sendto(pack_msg(FLAG_DATA, seq_byte_start=p["seq_start"], payload=p["payload"]), server_addr)
            total_send += 1
            now = time.time()
            first_send_time.setdefault(next_seq, now) # 该包第一次被发出的时间戳,setdefault方法确保是第一次发的序号,Karn
            log(f"→ 第 {next_seq} 个（第 {p['seq_start']}~{p['seq_end']} 字节）"
                f"client 端已经发送")
            if base == next_seq: # 窗口第一个包/窗口清空后重新发送,识别刚刚发送的是不是窗口中最老的包
                timer_start = now # 启动定时器
            next_seq += 1

        if timer_start is None:
            break

        # 2. 用当前 RTO 算剩余等待时间
        cur_rto = rtt_timer.timeout
        remaining = cur_rto - (time.time() - timer_start)
        if remaining <= 0:
            timeout_retx_count += 1 # 超时重传次数
            do_retransmit(f"超时（RTO={cur_rto*1000:.0f}ms）")
            timer_start    = time.time() # 给本轮重传启动计时器
            dup_ack_count  = 0
            in_recovery    = False
            continue

        # 3. 直接在主线程 recvfrom（带超时），不再开 ACK 接收线程
        sock.settimeout(remaining) # 没收到测超时异常
        try:
            raw, _ = sock.recvfrom(2048) # 收到消息
            recv_time = time.time() # ACK到达时间
        except socket.timeout:
            continue # 回到2超时重传
        except OSError:
            break

        pkt = unpack_msg(raw)
        if pkt is None or not (pkt["flags"] & FLAG_ACK):
            continue # 继续等待

        ack_end = pkt["ack_byte_end"]
        try:
            server_time = pkt["payload"].decode("ascii")
        except UnicodeDecodeError:
            server_time = "??-??-??"

        # 4(1). 重复 ACK → 快重传
        if last_ack_end is not None and ack_end == last_ack_end:
            dup_ack_count += 1
            log(f"← 收到重复累计 ACK ack_byte_end={ack_end}（第 {dup_ack_count} 次）")
            if (not in_recovery) and dup_ack_count >= DUP_ACK_THRESHOLD:
                fast_retx_count += 1
                do_retransmit(f"快重传（{DUP_ACK_THRESHOLD} 个重复 ACK）")
                in_recovery = True # 恢复标志，防止后续重复 ACK 再次触发快重传
                timer_start = time.time()
            continue

        # 4(2). 新 ACK：推进 base + 计 RTT + 更新自适应 RTO
        new_base = base
        while (new_base <= N_PACKETS and packets[new_base-1]["seq_end"] <= ack_end):
            p = packets[new_base - 1]
            if new_base not in retransmitted:
                sample_rtt_s = recv_time - first_send_time[new_base]
                rtt_ms = sample_rtt_s * 1000
                rtts.append(rtt_ms)
                rtt_timer.update(sample_rtt_s)
                log(f"← 第 {new_base} 个（第 {p['seq_start']}~{p['seq_end']} 字节）"
                    f"server 端已经收到，RTT 是 {rtt_ms:.2f} ms，"
                    f"server 时间 {server_time}  "
                    f"[EstRTT={rtt_timer.estimated_rtt*1000:.1f} "
                    f"DevRTT={rtt_timer.dev_rtt*1000:.1f} "
                    f"RTO={rtt_timer.timeout*1000:.0f}ms]")
            else: # 无法区分 ACK 是针对原始包还是重传包
                log(f"← 第 {new_base} 个（第 {p['seq_start']}~{p['seq_end']} 字节）"
                    f"server 端已经收到（重传过，按 Karn 算法不计 RTT），"
                    f"server 时间 {server_time}")
            new_base += 1 # 最后指向第一个尚未被确认的包序号

        last_ack_end = ack_end
        dup_ack_count = 0

        if new_base > base:
            base = new_base
            in_recovery = False
            if base > N_PACKETS: # 所有包都已确认
                timer_start = None
            else:
                timer_start = time.time()

    return rtts, total_send, fast_retx_count, timeout_retx_count

# 9.汇总
def print_summary(rtts, total_send, fast_retx, timeout_retx):
    log("================ 传输汇总 ================")
    log(f"目标成功包数             = {N_PACKETS}")
    log(f"实际发送 UDP 包数（含重传）= {total_send}")
    log(f"超时重传触发次数         = {timeout_retx}")
    log(f"快重传触发次数           = {fast_retx}")
    loss_rate = N_PACKETS / total_send * 100
    log(f"丢包率（任务书公式：30 ÷ 实发）= {loss_rate:.2f}%")
    if rtts:
        s = pd.Series(rtts)
        rtt_std = s.std() if len(rtts) > 1 else 0.0 # 防止只有一个样本返回NaN
        log(f"RTT 样本数  = {len(rtts)}")
        log(f"最大 RTT    = {s.max():.2f} ms")
        log(f"最小 RTT    = {s.min():.2f} ms")
        log(f"平均 RTT    = {s.mean():.2f} ms")
        log(f"RTT 标准差  = {rtt_std:.2f} ms")
    else:
        log("RTT 样本为空（所有包均经过重传）")

# 10.主入口
def create_client():
    server_ip, server_port, seed = input_check()
    init_log()

    if seed is not None:
        random.seed(seed)

    server_addr = (server_ip, server_port)
    log(f"参数: server={server_ip}:{server_port}  学号后4位={STUDENT_ID_LAST4}  "
        f"N={N_PACKETS}  窗口={WINDOW_BYTES}B  初始RTO={int(INITIAL_TIMEOUT_S*1000)}ms  "
        f"快重传阈值={DUP_ACK_THRESHOLD}  seed={seed}")
    log(f"自适应超时公式: RTO = EstRTT + 4·DevRTT  (α={ALPHA}, β={BETA})")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        if not handshake(sock, server_addr):
            return
        packets = prepare_packets()
        log(f"已准备 {N_PACKETS} 个数据包，总字节数={packets[-1]['seq_end']}")
        rtts, total_send, fast_retx, timeout_retx = gbn_send(sock, server_addr, packets)
        log(f"✔ 全部 {N_PACKETS} 个数据包均已确认")
        print_summary(rtts, total_send, fast_retx, timeout_retx)
        teardown(sock, server_addr)
    except Exception as e:
        log(f"未预期异常: {type(e).__name__}: {e}")
    finally:
        sock.close()
        log("socket 已关闭")
        if log_fp:
            log_fp.close()

if __name__ == '__main__':
    create_client()