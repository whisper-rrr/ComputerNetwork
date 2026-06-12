Task2 UDP Socket Programming —— README

一、项目简介
本项目基于 UDP 套接字与自定义应用层协议，在不可靠的 UDP 之上模拟实现 TCP 的可靠传输机制，完整流程包含「三次握手 → GBN 滑动窗口数据传输 → 四次挥手」三个阶段，支持多客户端并发，并通过 Wireshark 抓包可验证自定义首部与各阶段报文交互过程。
项目包含两个程序：
 - udpserver.py 服务端，负责接收数据、模拟丢包/损坏、回送累计 ACK 及服务端时间
 - udpclient.py 客户端，负责分包发送、滑动窗口控制、超时与快重传、统计 RTT
自定义应用层首部共 16 字节（采用网络字节序 / 大端序），4 种标志位区分报文：
   版本号  4 bit                  标识协议版本，当前为 1
   标志位  4 bit                  SYN=0x1  ACK=0x2  FIN=0x4  DATA=0x8（可按位组合）
   保留字段 B (1B)              预留扩展位，同时保证后续多字节字段自然对齐
   学号字段 H (2B)              学号后 4 位 XOR 0x5A3C，用于服务端身份合法性校验
   序列号  I (4B)                 本报文载荷在整体字节流中的起始字节号
   确认号  I (4B)                 累计确认到的最后一个连续字节号
   载荷长度 H (2B)              数据部分长度，范围 40~80 B
   校验和  H (2B)                16 位反码求和，覆盖整个首部 + 整个载荷
共 16 B

二、运行环境
1. 操作系统
   - Windows 10 / 11
   - Linux (Ubuntu 20.04+ 等) 
（上述系统均已测试可用，本项目客户端在 Windows、服务端在 Ubuntu 虚拟机上验证通过）
2. Python 版本
   - Python 3.7 及以上（推荐 3.9+）
3. 依赖库
   - pandas 用于汇总阶段计算 RTT 的最大/最小/均值/标准差，其余均使用 Python 标准库
   - 安装命令： pip install pandas
4. 可选工具
   - Wireshark：用于抓取并分析 UDP 报文，验证自定义首部与协议交互过程
   - Git：用于克隆/管理项目源码

三、文件清单
 udpclient.py                     客户端源代码
 udpserver.py                    服务端源代码
 udp_packet_capture.docx  说明文档（含 Wireshark 抓包分析、关键点与代码实现）
 readme.txt                        本说明文件

四、运行方法
建议先启动服务端，再启动客户端。
【1】启动服务器
    命令格式：
        python udpserver.py <port> [loss_rate] [corrupt_rate]
    参数说明：
        port           服务器监听端口，整数，范围 1024 ~ 65535
        loss_rate      可选，模拟丢包率，浮点数，范围 [0.0, 1.0)，默认 0.2
        corrupt_rate   可选，模拟损坏率，浮点数，范围 [0.0, 1.0)，默认 0.1
     （要求 loss_rate + corrupt_rate < 1.0）
    示例 1（使用默认丢包率/损坏率）：
        python udpserver.py 8888
    示例 2（指定丢包率 0.2、损坏率 0.05）：
        python udpserver.py 8888 0.2 0.05
    启动后服务器会输出：
        服务端已启动！监听端口: 8888  模拟丢包率: 20.0%  模拟损坏率: 5.0%
        [多线程] 主线程 = dispatcher，每个 client 独立 Session 线程
        [不可靠模拟] 仅作用于 client→server 方向的 DATA 报文……
【2】启动客户端
    命令格式：
        python udpclient.py <server_ip> <server_port> [seed]
    参数说明：
        server_ip      服务器 IP 地址（本机测试可填 127.0.0.1）
        server_port   服务器监听端口（与服务端保持一致）
        seed             可选，随机种子，用于复现同一组随机载荷与长度
    示例 1（不指定 seed）：
        python udpclient.py 192.168.70.128 8888
    示例 2（指定 seed=1234，使每次发送的数据包内容可复现）：
        python udpclient.py 192.168.70.128 8888 1234
    运行结束后：
        - 终端会按顺序打印每个数据包的发送、确认（含 RTT 与服务端时间）、超时与重传事件
        - 当前目录生成 run_log.txt（详细运行日志，时间戳精确到微秒，可与 Wireshark 印证）
        - 客户端最后输出【传输汇总】，包括： 实际发送的 UDP 包数（含重传） 超时重传 / 快重传 触发次数 丢包率（任务书公式：30 ÷ 实际发送数） 最大 RTT / 最小 RTT / 平均 RTT / RTT 标准差（由 pandas 计算）

五、关键配置项（可在 udpclient.py 顶部修改）
    N_PACKETS                   = 30               要成功送达的数据包总数
    WINDOW_BYTES           = 400             发送窗口大小（字节）
    MIN_PAYLOAD              = 40               单包最小载荷字节数
    MAX_PAYLOAD             = 80                单包最大载荷字节数
    INITIAL_TIMEOUT_S      = 0.3               初始 RTO（自适应公式启动前使用）
    DUP_ACK_THRESHOLD  = 3                 触发快重传所需的重复 ACK 个数
    ALPHA, BETA                 = 0.125, 0.25   RTT 估计平滑因子（同 RFC 6298）
    TIMEOUT_MIN_S           = 0.05             RTO 下限
    TIMEOUT_MAX_S           = 2.0              RTO 上限
    自适应超时公式： RTO = EstRTT + 4 · DevRTT

六、Git 仓库地址
   https://github.com/whisper-rrr/ComputerNetwork.git
